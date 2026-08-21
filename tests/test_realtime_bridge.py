import asyncio
import base64
import json
import logging
from typing import Any

import pytest
from fastapi import WebSocketDisconnect

from app.agent.tools import ToolDispatcher
from app.domain.calls import CallRuntime
from app.services.audio_codec import PcmuPassthroughCodec
from app.services.media_stream import MediaStreamSession
from app.services.openai_realtime import (
    MalformedRealtimeEvent,
    RealtimeAPIError,
    RealtimeDisconnected,
)
from app.services.realtime_bridge import RealtimeAudioBridge
from tests.helpers import call_configuration

CALL_SID = "CA11111111111111111111111111111111"
STREAM_SID = "MZ11111111111111111111111111111111"
AUDIO = base64.b64encode(bytes(160)).decode()


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
        self.truncations: list[dict[str, Any]] = []
        self.tool_outputs: list[dict[str, Any]] = []
        self.response_requests: list[bool] = []

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

    async def truncate_conversation_item(
        self, *, item_id: str, content_index: int, audio_end_ms: int
    ) -> None:
        self.truncations.append(
            {
                "item_id": item_id,
                "content_index": content_index,
                "audio_end_ms": audio_end_ms,
            }
        )

    async def submit_tool_output(self, *, call_id: str, result: dict[str, Any]) -> None:
        self.tool_outputs.append({"call_id": call_id, "result": result})

    async def request_response(self, *, finishing: bool = False) -> None:
        self.response_requests.append(finishing)

    async def close(self) -> None:
        self.closed = True


def twilio_event(event: str, **contents: Any) -> str:
    return json.dumps({"event": event, "streamSid": STREAM_SID, **contents})


def audio_delta(response_id: str, item_id: str) -> dict[str, Any]:
    return {
        "type": "response.output_audio.delta",
        "response_id": response_id,
        "item_id": item_id,
        "content_index": 0,
        "delta": AUDIO,
    }


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
        await realtime.incoming.put(audio_delta("response-1", "item-1"))

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
            },
            {
                "event": "mark",
                "streamSid": STREAM_SID,
                "mark": {"name": "assistant-audio-1"},
            },
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
        await realtime.incoming.put(audio_delta("response-1", "item-1"))
        await realtime.incoming.put({"type": "input_audio_buffer.speech_started"})
        await realtime.incoming.put(RealtimeDisconnected("done"))

        bridge = RealtimeAudioBridge(
            twilio, realtime, media_session(), PcmuPassthroughCodec()
        )
        await bridge.run()

        assert twilio.sent[-1] == {"event": "clear", "streamSid": STREAM_SID}
        assert realtime.truncations == [
            {"item_id": "item-1", "content_index": 0, "audio_end_ms": 0}
        ]
        assert bridge.playback.is_playing is False

    asyncio.run(scenario())


def test_repeated_interruptions_clear_and_truncate_each_response() -> None:
    async def scenario() -> None:
        twilio = FakeTwilioSocket()
        realtime = FakeRealtimeSession()
        await realtime.incoming.put(audio_delta("response-1", "item-1"))
        await realtime.incoming.put({"type": "input_audio_buffer.speech_started"})
        await realtime.incoming.put(
            {"type": "response.cancelled", "response": {"id": "response-1"}}
        )
        await realtime.incoming.put(audio_delta("response-2", "item-2"))
        await realtime.incoming.put({"type": "input_audio_buffer.speech_started"})
        await realtime.incoming.put(
            {"type": "response.cancelled", "response": {"id": "response-2"}}
        )
        await realtime.incoming.put(RealtimeDisconnected("done"))

        bridge = RealtimeAudioBridge(
            twilio, realtime, media_session(), PcmuPassthroughCodec()
        )
        await bridge.run()

        assert [message["event"] for message in twilio.sent] == [
            "media",
            "mark",
            "clear",
            "media",
            "mark",
            "clear",
        ]
        assert realtime.truncations == [
            {"item_id": "item-1", "content_index": 0, "audio_end_ms": 0},
            {"item_id": "item-2", "content_index": 0, "audio_end_ms": 0},
        ]
        assert bridge.playback.is_playing is False

    asyncio.run(scenario())


def test_stale_audio_does_not_leak_after_interruption() -> None:
    async def scenario() -> None:
        twilio = FakeTwilioSocket()
        realtime = FakeRealtimeSession()
        await realtime.incoming.put(audio_delta("response-1", "item-1"))
        await realtime.incoming.put({"type": "input_audio_buffer.speech_started"})
        await realtime.incoming.put(audio_delta("response-1", "item-1"))
        await realtime.incoming.put(audio_delta("response-2", "item-2"))
        await realtime.incoming.put(RealtimeDisconnected("done"))

        bridge = RealtimeAudioBridge(
            twilio, realtime, media_session(), PcmuPassthroughCodec()
        )
        await bridge.run()

        media_messages = [
            message for message in twilio.sent if message["event"] == "media"
        ]
        assert len(media_messages) == 2
        assert realtime.truncations == [
            {"item_id": "item-1", "content_index": 0, "audio_end_ms": 0}
        ]

    asyncio.run(scenario())


def test_interruption_after_output_done_still_clears_unplayed_audio() -> None:
    async def scenario() -> None:
        twilio = FakeTwilioSocket()
        realtime = FakeRealtimeSession()
        await realtime.incoming.put(audio_delta("response-1", "item-1"))
        await realtime.incoming.put(
            {"type": "response.output_audio.done", "response_id": "response-1"}
        )
        await realtime.incoming.put({"type": "input_audio_buffer.speech_started"})
        await realtime.incoming.put(RealtimeDisconnected("done"))

        bridge = RealtimeAudioBridge(
            twilio, realtime, media_session(), PcmuPassthroughCodec()
        )
        await bridge.run()

        assert twilio.sent[-1] == {"event": "clear", "streamSid": STREAM_SID}
        assert realtime.truncations == [
            {"item_id": "item-1", "content_index": 0, "audio_end_ms": 0}
        ]

    asyncio.run(scenario())


def test_finish_call_waits_until_final_goodbye_mark_is_played() -> None:
    async def scenario() -> None:
        twilio = FakeTwilioSocket()
        realtime = FakeRealtimeSession()
        runtime = CallRuntime(call_configuration())
        await realtime.incoming.put(
            {
                "type": "response.done",
                "response": {
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "name": "finish_call",
                            "call_id": "tool-call-1",
                            "arguments": json.dumps({"reason": "Objective completed"}),
                        }
                    ],
                },
            }
        )
        await realtime.incoming.put(audio_delta("final-response", "final-item"))
        await realtime.incoming.put(
            {
                "type": "response.output_audio.done",
                "response_id": "final-response",
            }
        )
        bridge = RealtimeAudioBridge(
            twilio,
            realtime,
            media_session(),
            PcmuPassthroughCodec(),
            tool_dispatcher=ToolDispatcher(runtime),
        )

        task = asyncio.create_task(bridge.run())
        for _ in range(100):
            marks = [message for message in twilio.sent if message["event"] == "mark"]
            if marks:
                break
            await asyncio.sleep(0)
        assert marks
        assert task.done() is False
        await twilio.incoming.put(
            twilio_event("mark", mark={"name": marks[-1]["mark"]["name"]})
        )
        await asyncio.wait_for(task, timeout=1)

        assert runtime.finish_requested is True
        assert realtime.response_requests == [True]
        assert realtime.tool_outputs[0]["call_id"] == "tool-call-1"
        assert realtime.closed is True

    asyncio.run(scenario())


def test_unknown_model_tool_is_rejected_without_dynamic_execution() -> None:
    async def scenario() -> None:
        twilio = FakeTwilioSocket()
        realtime = FakeRealtimeSession()
        runtime = CallRuntime(call_configuration())
        await realtime.incoming.put(
            {
                "type": "response.done",
                "response": {
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "name": "shell_exec",
                            "call_id": "tool-call-2",
                            "arguments": "{}",
                        }
                    ],
                },
            }
        )
        await realtime.incoming.put(RealtimeDisconnected("done"))
        bridge = RealtimeAudioBridge(
            twilio,
            realtime,
            media_session(),
            PcmuPassthroughCodec(),
            tool_dispatcher=ToolDispatcher(runtime),
        )

        await bridge.run()

        assert realtime.tool_outputs == [
            {
                "call_id": "tool-call-2",
                "result": {"ok": False, "error": "Tool is not allowed: shell_exec"},
            }
        ]
        assert realtime.response_requests == [False]

    asyncio.run(scenario())
