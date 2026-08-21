import asyncio
import json
import logging
from typing import Any

import pytest
from fastapi import WebSocketDisconnect

from app.services.audio_codec import PcmuPassthroughCodec
from app.services.media_stream import MediaStreamSession
from app.services.openai_realtime import (
    MalformedRealtimeEvent,
    RealtimeAPIError,
    RealtimeDisconnected,
)
from app.services.realtime_bridge import RealtimeAudioBridge

CALL_SID = "CA11111111111111111111111111111111"
STREAM_SID = "MZ11111111111111111111111111111111"
AUDIO = "AAECAw=="


def media_session() -> MediaStreamSession:
    session = MediaStreamSession()
    session.handle_event(
        {
            "event": "start",
            "start": {
                "callSid": CALL_SID,
                "streamSid": STREAM_SID,
                "mediaFormat": {
                    "encoding": "audio/x-mulaw",
                    "sampleRate": 8000,
                    "channels": 1,
                },
                "customParameters": {"internal_call_id": "internal-1"},
            },
        }
    )
    return session


class FakeTwilioSocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str | Exception] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.output_sent = asyncio.Event()
        self.wait_before_stop = False

    async def receive_text(self) -> str:
        item = await self.incoming.get()
        if item == "WAIT_FOR_OUTPUT":
            await self.output_sent.wait()
            item = await self.incoming.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def send_json(self, data: Any) -> None:
        self.sent.append(data)
        self.output_sent.set()


class FakeRealtimeSession:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[dict[str, Any] | Exception] = asyncio.Queue()
        self.input_audio: list[str] = []
        self.connected = False
        self.configured = False
        self.closed = False

    async def connect(self) -> None:
        self.connected = True

    async def configure(self) -> None:
        self.configured = True

    async def append_input_audio(self, payload: str) -> None:
        self.input_audio.append(payload)

    async def receive_event(self) -> dict[str, Any]:
        item = await self.incoming.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True


def twilio_event(event: str, **contents: Any) -> str:
    return json.dumps({"event": event, "streamSid": STREAM_SID, **contents})


def test_bridge_relays_audio_in_both_directions_and_tears_down() -> None:
    async def scenario() -> None:
        twilio = FakeTwilioSocket()
        realtime = FakeRealtimeSession()
        await twilio.incoming.put(
            twilio_event(
                "media",
                media={"track": "inbound", "payload": AUDIO},
            )
        )
        await twilio.incoming.put("WAIT_FOR_OUTPUT")
        await twilio.incoming.put(twilio_event("stop", stop={}))
        await realtime.incoming.put(
            {"type": "response.output_audio.delta", "delta": AUDIO}
        )

        bridge = RealtimeAudioBridge(
            twilio, realtime, media_session(), PcmuPassthroughCodec()
        )
        await bridge.run()

        assert realtime.connected is True
        assert realtime.configured is True
        assert realtime.input_audio == [AUDIO]
        assert twilio.sent == [
            {
                "event": "media",
                "streamSid": STREAM_SID,
                "media": {"payload": AUDIO},
            }
        ]
        assert realtime.closed is True

    asyncio.run(scenario())


def test_openai_error_cancels_twilio_receiver_and_closes_session(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        twilio = FakeTwilioSocket()
        realtime = FakeRealtimeSession()
        await realtime.incoming.put(
            RealtimeAPIError("rate_limit_exceeded", "slow down")
        )
        caplog.set_level(logging.INFO)

        bridge = RealtimeAudioBridge(
            twilio, realtime, media_session(), PcmuPassthroughCodec()
        )
        await bridge.run()

        assert realtime.closed is True
        assert "OPENAI_REALTIME_ERROR code=rate_limit_exceeded" in caplog.text

    asyncio.run(scenario())


def test_twilio_disconnect_cancels_openai_receiver() -> None:
    async def scenario() -> None:
        twilio = FakeTwilioSocket()
        realtime = FakeRealtimeSession()
        await twilio.incoming.put(WebSocketDisconnect(code=1000))

        bridge = RealtimeAudioBridge(
            twilio, realtime, media_session(), PcmuPassthroughCodec()
        )
        await bridge.run()

        assert realtime.closed is True

    asyncio.run(scenario())


def test_unknown_and_malformed_openai_events_do_not_crash_or_log_audio(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        twilio = FakeTwilioSocket()
        realtime = FakeRealtimeSession()
        await realtime.incoming.put(MalformedRealtimeEvent("bad event"))
        await realtime.incoming.put({"type": "future.event", "payload": AUDIO})
        await realtime.incoming.put(RealtimeDisconnected("done"))
        caplog.set_level(logging.DEBUG)

        bridge = RealtimeAudioBridge(
            twilio, realtime, media_session(), PcmuPassthroughCodec()
        )
        await bridge.run()

        assert "OPENAI_REALTIME_MALFORMED" in caplog.text
        assert "OPENAI_REALTIME_UNKNOWN_EVENT type=future.event" in caplog.text
        assert AUDIO not in caplog.text
        assert realtime.closed is True

    asyncio.run(scenario())


def test_speech_started_clears_queued_twilio_audio() -> None:
    async def scenario() -> None:
        twilio = FakeTwilioSocket()
        realtime = FakeRealtimeSession()
        await realtime.incoming.put({"type": "input_audio_buffer.speech_started"})
        await realtime.incoming.put(RealtimeDisconnected("done"))

        bridge = RealtimeAudioBridge(
            twilio, realtime, media_session(), PcmuPassthroughCodec()
        )
        await bridge.run()

        assert twilio.sent == [{"event": "clear", "streamSid": STREAM_SID}]

    asyncio.run(scenario())
