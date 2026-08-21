"""Concurrent audio relay between Twilio and OpenAI Realtime."""

import asyncio
import json
import logging
from typing import Any, Protocol

from fastapi.websockets import WebSocketDisconnect

from app.services.audio_codec import AudioCodec, InvalidAudioPayload
from app.services.media_stream import MalformedMediaEvent, MediaStreamSession
from app.services.openai_realtime import (
    MalformedRealtimeEvent,
    OpenAIRealtimeSession,
    RealtimeAPIError,
    RealtimeDisconnected,
)
from app.services.playback import AssistantPlaybackTracker

logger = logging.getLogger(__name__)


class TwilioMediaSocket(Protocol):
    """Twilio WebSocket operations needed by the bridge."""

    async def receive_text(self) -> str: ...

    async def send_json(self, data: Any) -> None: ...


class _BridgeFinished(Exception):
    """Internal structured-concurrency signal used to cancel sibling tasks."""


class RealtimeAudioBridge:
    """Relay compatible audio concurrently and tear down both directions together."""

    def __init__(
        self,
        twilio: TwilioMediaSocket,
        realtime: OpenAIRealtimeSession,
        media_session: MediaStreamSession,
        codec: AudioCodec,
    ) -> None:
        if media_session.stream_sid is None:
            raise ValueError("Twilio start event must be processed before bridging")
        self.twilio = twilio
        self.realtime = realtime
        self.media_session = media_session
        self.codec = codec
        self.playback = AssistantPlaybackTracker()

    async def run(self) -> None:
        """Run both relay directions until either side stops or disconnects."""

        try:
            await self.realtime.connect()
            await self.realtime.configure()
            try:
                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(self._twilio_to_openai())
                    tasks.create_task(self._openai_to_twilio())
            except* _BridgeFinished:
                pass
        except RealtimeDisconnected:
            logger.info("REALTIME_BRIDGE_OPENAI_DISCONNECTED")
        finally:
            await self.realtime.close()
            logger.info(
                "REALTIME_BRIDGE_STOPPED call_sid=%s stream_sid=%s",
                self.media_session.call_sid,
                self.media_session.stream_sid,
            )

    async def _twilio_to_openai(self) -> None:
        try:
            while True:
                raw_message = await self.twilio.receive_text()
                try:
                    message = json.loads(raw_message)
                    should_continue = self.media_session.handle_event(message)
                except json.JSONDecodeError:
                    logger.warning("MEDIA_STREAM_MALFORMED invalid_json")
                    continue
                except MalformedMediaEvent as exc:
                    logger.warning("MEDIA_STREAM_MALFORMED reason=%s", exc)
                    continue

                if message.get("event") == "media":
                    media = message.get("media")
                    payload = media.get("payload") if isinstance(media, dict) else None
                    try:
                        encoded_audio = self.codec.twilio_to_openai(payload)
                    except InvalidAudioPayload as exc:
                        logger.warning("MEDIA_STREAM_MALFORMED reason=%s", exc)
                        continue
                    await self.realtime.append_input_audio(encoded_audio)
                elif message.get("event") == "mark":
                    mark = message.get("mark")
                    name = mark.get("name") if isinstance(mark, dict) else None
                    if isinstance(name, str):
                        self.playback.acknowledge_mark(name)
                if not should_continue:
                    raise _BridgeFinished
        except WebSocketDisconnect as exc:
            logger.info(
                "MEDIA_STREAM_DISCONNECTED call_sid=%s stream_sid=%s "
                "packets=%d bytes=%d",
                self.media_session.call_sid,
                self.media_session.stream_sid,
                self.media_session.media_packets,
                self.media_session.media_bytes,
            )
            logger.info("REALTIME_BRIDGE_TWILIO_DISCONNECTED")
            raise _BridgeFinished from exc
        except RealtimeDisconnected as exc:
            logger.info("REALTIME_BRIDGE_OPENAI_DISCONNECTED")
            raise _BridgeFinished from exc

    async def _openai_to_twilio(self) -> None:
        while True:
            try:
                event = await self.realtime.receive_event()
            except MalformedRealtimeEvent as exc:
                logger.warning("OPENAI_REALTIME_MALFORMED reason=%s", exc)
                continue
            except RealtimeAPIError as exc:
                logger.error("OPENAI_REALTIME_ERROR code=%s", exc.code)
                raise _BridgeFinished from exc
            except RealtimeDisconnected as exc:
                logger.info("REALTIME_BRIDGE_OPENAI_DISCONNECTED")
                raise _BridgeFinished from exc

            event_type = event["type"]
            if event_type == "response.output_audio.delta":
                delta = event.get("delta")
                response_id = event.get("response_id")
                item_id = event.get("item_id")
                content_index = event.get("content_index", 0)
                if (
                    not isinstance(response_id, str)
                    or not isinstance(item_id, str)
                    or not isinstance(content_index, int)
                ):
                    logger.warning(
                        "OPENAI_REALTIME_MALFORMED reason=audio_delta_identifiers"
                    )
                    continue
                try:
                    encoded_audio = self.codec.openai_to_twilio(delta)
                    duration_ms = self.codec.duration_ms(encoded_audio)
                except InvalidAudioPayload as exc:
                    logger.warning("OPENAI_REALTIME_MALFORMED reason=%s", exc)
                    continue
                mark_name = self.playback.add_audio(
                    response_id=response_id,
                    item_id=item_id,
                    content_index=content_index,
                    duration_ms=duration_ms,
                )
                if mark_name is None:
                    logger.debug(
                        "OPENAI_REALTIME_STALE_AUDIO_DROPPED response_id=%s",
                        response_id,
                    )
                    continue
                await self.twilio.send_json(
                    {
                        "event": "media",
                        "streamSid": self.media_session.stream_sid,
                        "media": {"payload": encoded_audio},
                    }
                )
                await self.twilio.send_json(
                    {
                        "event": "mark",
                        "streamSid": self.media_session.stream_sid,
                        "mark": {"name": mark_name},
                    }
                )
            elif event_type == "input_audio_buffer.speech_started":
                await self._interrupt_assistant()
            elif event_type == "response.output_audio.done":
                response_id = event.get("response_id")
                if isinstance(response_id, str):
                    self.playback.output_done(response_id)
            elif event_type == "response.cancelled":
                response_id = self._response_id(event)
                if response_id is not None:
                    self.playback.cancel_response(response_id)
                logger.info("OPENAI_REALTIME_RESPONSE_CANCELLED")
            elif event_type == "response.done":
                response = event.get("response")
                if isinstance(response, dict) and response.get("status") == "cancelled":
                    response_id = self._response_id(event)
                    if response_id is not None:
                        self.playback.cancel_response(response_id)
                    logger.info("OPENAI_REALTIME_RESPONSE_CANCELLED")
                else:
                    logger.debug("OPENAI_REALTIME_EVENT type=response.done")
            elif event_type == "rate_limits.updated":
                logger.info("OPENAI_REALTIME_RATE_LIMITS_UPDATED")
            elif event_type in {
                "session.created",
                "session.updated",
                "response.created",
            }:
                logger.debug("OPENAI_REALTIME_EVENT type=%s", event_type)
            else:
                logger.debug("OPENAI_REALTIME_UNKNOWN_EVENT type=%s", event_type)

    async def _interrupt_assistant(self) -> None:
        point = self.playback.interrupt()
        if point is None:
            return
        logger.info(
            "ASSISTANT_INTERRUPTED item_id=%s audio_end_ms=%d",
            point.item_id,
            point.audio_end_ms,
        )
        try:
            await self.realtime.truncate_conversation_item(
                item_id=point.item_id,
                content_index=point.content_index,
                audio_end_ms=point.audio_end_ms,
            )
        finally:
            await self.twilio.send_json(
                {
                    "event": "clear",
                    "streamSid": self.media_session.stream_sid,
                }
            )

    @staticmethod
    def _response_id(event: dict[str, Any]) -> str | None:
        response_id = event.get("response_id")
        if isinstance(response_id, str):
            return response_id
        response = event.get("response")
        if isinstance(response, dict) and isinstance(response.get("id"), str):
            return response["id"]
        return None
