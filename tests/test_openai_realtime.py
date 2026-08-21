import asyncio
import json
from typing import Any

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.services.openai_realtime import (
    INITIAL_INSTRUCTIONS,
    MalformedRealtimeEvent,
    OpenAIRealtimeSession,
    RealtimeAPIError,
)


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
        await session.connect()
        await session.configure()
        await session.close()

        assert connection == {
            "url": "wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1",
            "additional_headers": {"Authorization": "Bearer test-api-key"},
        }
        update, initial_response = socket.sent
        assert update["type"] == "session.update"
        assert update["session"]["instructions"] == INITIAL_INSTRUCTIONS
        assert update["session"]["output_modalities"] == ["audio"]
        assert update["session"]["audio"] == {
            "input": {
                "format": {"type": "audio/pcmu"},
                "turn_detection": {
                    "type": "server_vad",
                    "create_response": True,
                    "interrupt_response": True,
                },
            },
            "output": {"format": {"type": "audio/pcmu"}, "voice": "marin"},
        }
        assert initial_response == {"type": "response.create"}
        assert socket.closed is True

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
