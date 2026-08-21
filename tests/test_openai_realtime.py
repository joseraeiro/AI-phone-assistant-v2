import asyncio
import json
from typing import Any

import pytest
from pydantic import SecretStr

from app.agent.instructions import (
    build_agent_instructions,
    build_first_utterance_instructions,
)
from app.config import Settings
from app.services.openai_realtime import (
    MalformedRealtimeEvent,
    OpenAIRealtimeSession,
    RealtimeAPIError,
)
from tests.helpers import call_configuration


class FakeRealtimeSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.incoming: asyncio.Queue[str | bytes] = asyncio.Queue()
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str | bytes:
        return await self.incoming.get()

    async def close(self) -> None:
        self.closed = True


def realtime_settings() -> Settings:
    return Settings(
        openai_api_key=SecretStr("test-api-key"),
        openai_realtime_model="gpt-realtime-2.1",
        openai_realtime_voice="marin",
    )


def test_connect_and_configure_current_pcmu_session() -> None:
    async def scenario() -> None:
        socket = FakeRealtimeSocket()
        connection: dict[str, Any] = {}

        async def connector(url: str, **kwargs: Any) -> FakeRealtimeSocket:
            connection.update(url=url, **kwargs)
            return socket

        session = OpenAIRealtimeSession(realtime_settings(), connector=connector)
        call = call_configuration()
        session.configure_agent(call)
        await session.connect()
        await session.configure()
        await session.close()

        assert connection == {
            "url": "wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1",
            "additional_headers": {"Authorization": "Bearer test-api-key"},
        }
        update, initial_response = socket.sent
        assert update["type"] == "session.update"
        assert update["session"]["instructions"] == build_agent_instructions(call)
        assert update["session"]["output_modalities"] == ["audio"]
        assert update["session"]["audio"] == {
            "input": {
                "format": {"type": "audio/pcmu"},
                "transcription": {"model": "gpt-live-transcribe"},
                "turn_detection": {
                    "type": "semantic_vad",
                    "create_response": True,
                    "interrupt_response": True,
                    "eagerness": "auto",
                },
            },
            "output": {"format": {"type": "audio/pcmu"}, "voice": "marin"},
        }
        assert initial_response == {
            "type": "response.create",
            "response": {
                "output_modalities": ["audio"],
                "instructions": build_first_utterance_instructions(call),
            },
        }
        assert socket.closed is True

    asyncio.run(scenario())


def test_server_vad_settings_are_configurable() -> None:
    async def scenario() -> None:
        socket = FakeRealtimeSocket()

        async def connector(url: str, **kwargs: Any) -> FakeRealtimeSocket:
            return socket

        settings = realtime_settings().model_copy(
            update={
                "openai_realtime_vad_type": "server_vad",
                "openai_realtime_vad_threshold": 0.6,
                "openai_realtime_vad_prefix_padding_ms": 400,
                "openai_realtime_vad_silence_duration_ms": 800,
            }
        )
        session = OpenAIRealtimeSession(settings, connector=connector)
        session.configure_agent(call_configuration())
        await session.connect()
        await session.configure()

        turn_detection = socket.sent[0]["session"]["audio"]["input"]["turn_detection"]
        assert turn_detection == {
            "type": "server_vad",
            "create_response": True,
            "interrupt_response": True,
            "threshold": 0.6,
            "prefix_padding_ms": 400,
            "silence_duration_ms": 800,
        }

    asyncio.run(scenario())


def test_truncate_conversation_item_uses_confirmed_playback_boundary() -> None:
    async def scenario() -> None:
        socket = FakeRealtimeSocket()

        async def connector(url: str, **kwargs: Any) -> FakeRealtimeSocket:
            return socket

        session = OpenAIRealtimeSession(realtime_settings(), connector=connector)
        await session.connect()
        await session.truncate_conversation_item(
            item_id="item-1", content_index=0, audio_end_ms=240
        )

        assert socket.sent == [
            {
                "type": "conversation.item.truncate",
                "item_id": "item-1",
                "content_index": 0,
                "audio_end_ms": 240,
            }
        ]

    asyncio.run(scenario())


def test_append_audio_uses_current_input_buffer_event() -> None:
    async def scenario() -> None:
        socket = FakeRealtimeSocket()

        async def connector(url: str, **kwargs: Any) -> FakeRealtimeSocket:
            return socket

        session = OpenAIRealtimeSession(realtime_settings(), connector=connector)
        await session.connect()
        await session.append_input_audio("AAECAw==")

        assert socket.sent == [
            {"type": "input_audio_buffer.append", "audio": "AAECAw=="}
        ]

    asyncio.run(scenario())


def test_receive_event_handles_error_and_malformed_json() -> None:
    async def scenario() -> None:
        socket = FakeRealtimeSocket()

        async def connector(url: str, **kwargs: Any) -> FakeRealtimeSocket:
            return socket

        session = OpenAIRealtimeSession(realtime_settings(), connector=connector)
        await session.connect()
        await socket.incoming.put("not-json")
        with pytest.raises(MalformedRealtimeEvent):
            await session.receive_event()

        await socket.incoming.put(
            json.dumps(
                {
                    "type": "error",
                    "error": {"code": "rate_limit_exceeded", "message": "slow down"},
                }
            )
        )
        with pytest.raises(RealtimeAPIError, match="rate_limit_exceeded"):
            await session.receive_event()

    asyncio.run(scenario())


def test_tool_output_and_final_response_use_realtime_event_shapes() -> None:
    async def scenario() -> None:
        socket = FakeRealtimeSocket()

        async def connector(url: str, **kwargs: Any) -> FakeRealtimeSocket:
            return socket

        session = OpenAIRealtimeSession(realtime_settings(), connector=connector)
        await session.connect()
        await session.submit_tool_output(
            call_id="call-1", result={"saved": True, "category": "hours"}
        )
        await session.request_response(finishing=True)

        assert socket.sent[0] == {
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": '{"saved": true, "category": "hours"}',
            },
        }
        assert socket.sent[1]["type"] == "response.create"
        assert socket.sent[1]["response"]["output_modalities"] == ["audio"]
        assert socket.sent[1]["response"]["tool_choice"] == "none"
        assert "thank-you and goodbye" in socket.sent[1]["response"]["instructions"]

    asyncio.run(scenario())
