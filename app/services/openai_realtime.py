"""Server-side OpenAI Realtime WebSocket session."""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import urlencode

from websockets.asyncio.client import connect as websocket_connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from app.agent.instructions import (
    FINAL_UTTERANCE_INSTRUCTIONS,
    build_agent_instructions,
    build_first_utterance_instructions,
)
from app.agent.tools import realtime_tool_definitions, serialize_tool_result
from app.config import Settings
from app.domain.calls import CallConfiguration

logger = logging.getLogger(__name__)

REALTIME_ENDPOINT = "wss://api.openai.com/v1/realtime"


class RealtimeSocket(Protocol):
    """WebSocket operations used by the Realtime session."""

    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


RealtimeConnector = Callable[..., Awaitable[RealtimeSocket]]


class RealtimeConfigurationError(RuntimeError):
    """Raised when required OpenAI configuration is absent."""


class RealtimeDisconnected(ConnectionError):
    """Raised when OpenAI closes the Realtime connection."""


class MalformedRealtimeEvent(ValueError):
    """Raised for non-JSON or structurally invalid server events."""


class RealtimeAPIError(RuntimeError):
    """Raised when the Realtime API emits an error event."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class OpenAIRealtimeSession:
    """Connect, configure, exchange audio events, and close one session."""

    def __init__(
        self,
        settings: Settings,
        connector: RealtimeConnector = websocket_connect,
    ) -> None:
        self.settings = settings
        self._connector = connector
        self._socket: RealtimeSocket | None = None
        self._closed = False
        self._agent_instructions: str | None = None
        self._first_utterance_instructions: str | None = None
        self._model = settings.openai_realtime_model
        self._voice = settings.openai_realtime_voice

    def configure_agent(self, call: CallConfiguration) -> None:
        """Attach the centralized prompt and allowlisted tools before connecting."""

        self._agent_instructions = build_agent_instructions(call)
        self._first_utterance_instructions = build_first_utterance_instructions(call)
        self._model = call.realtime_model or self.settings.openai_realtime_model
        self._voice = call.voice or self.settings.openai_realtime_voice

    async def connect(self) -> None:
        """Open an authenticated server-to-server Realtime WebSocket."""

        if self.settings.openai_api_key is None:
            raise RealtimeConfigurationError("OPENAI_API_KEY is required")
        query = urlencode({"model": self._model})
        try:
            self._socket = await self._connector(
                f"{REALTIME_ENDPOINT}?{query}",
                additional_headers={
                    "Authorization": (
                        f"Bearer {self.settings.openai_api_key.get_secret_value()}"
                    )
                },
            )
        except (OSError, WebSocketException) as exc:
            raise RealtimeDisconnected("OpenAI Realtime connection failed") from exc
        logger.info(
            "OPENAI_REALTIME_CONNECTED model=%s", self._model
        )

    async def configure(self) -> None:
        """Configure direct PCMU speech-to-speech and prompt the first response."""

        if (
            self._agent_instructions is None
            or self._first_utterance_instructions is None
        ):
            raise RealtimeConfigurationError("Call agent instructions are required")
        await self._send_event(
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "model": self._model,
                    "instructions": self._agent_instructions,
                    "output_modalities": ["audio"],
                    "tools": realtime_tool_definitions(),
                    "tool_choice": "auto",
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcmu"},
                            "transcription": {
                                "model": self.settings.openai_transcription_model
                            },
                            "turn_detection": self._turn_detection(),
                        },
                        "output": {
                            "format": {"type": "audio/pcmu"},
                            "voice": self._voice,
                        },
                    },
                },
            }
        )
        await self._send_event(
            {
                "type": "response.create",
                "response": {
                    "output_modalities": ["audio"],
                    "instructions": self._first_utterance_instructions,
                },
            }
        )

    async def append_input_audio(self, payload: str) -> None:
        """Append one base64 PCMU chunk to the model input buffer."""

        await self._send_event({"type": "input_audio_buffer.append", "audio": payload})

    async def truncate_conversation_item(
        self, *, item_id: str, content_index: int, audio_end_ms: int
    ) -> None:
        """Remove assistant audio that Twilio did not confirm as played."""

        await self._send_event(
            {
                "type": "conversation.item.truncate",
                "item_id": item_id,
                "content_index": content_index,
                "audio_end_ms": audio_end_ms,
            }
        )

    async def submit_tool_output(self, *, call_id: str, result: dict[str, Any]) -> None:
        """Return one validated internal tool result to the model conversation."""

        await self._send_event(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": serialize_tool_result(result),
                },
            }
        )

    async def request_response(self, *, finishing: bool = False) -> None:
        """Ask the model to continue, optionally limiting it to a final goodbye."""

        event: dict[str, Any] = {"type": "response.create"}
        if finishing:
            event["response"] = {
                "output_modalities": ["audio"],
                "instructions": FINAL_UTTERANCE_INSTRUCTIONS,
                "tool_choice": "none",
            }
        await self._send_event(event)

    async def receive_event(self) -> dict[str, Any]:
        """Receive one server event and turn disconnect/error states into exceptions."""

        socket = self._require_socket()
        try:
            raw_event = await socket.recv()
        except ConnectionClosed as exc:
            raise RealtimeDisconnected("OpenAI Realtime disconnected") from exc
        if isinstance(raw_event, bytes):
            try:
                raw_event = raw_event.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise MalformedRealtimeEvent("event is not UTF-8 JSON") from exc
        try:
            event = json.loads(raw_event)
        except (json.JSONDecodeError, TypeError) as exc:
            raise MalformedRealtimeEvent("event is not valid JSON") from exc
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise MalformedRealtimeEvent("event must be an object with a type")
        if event["type"] == "error":
            error = event.get("error") if isinstance(event.get("error"), dict) else {}
            code = str(error.get("code") or "realtime_error")
            message = str(error.get("message") or "OpenAI Realtime API error")
            raise RealtimeAPIError(code=code, message=message)
        return event

    async def close(self) -> None:
        """Close at most once and discard the socket reference."""

        if self._closed:
            return
        self._closed = True
        socket, self._socket = self._socket, None
        if socket is not None:
            try:
                await socket.close()
            except (OSError, WebSocketException):
                logger.warning("OPENAI_REALTIME_CLOSE_FAILED")
        logger.info("OPENAI_REALTIME_CLOSED")

    async def _send_event(self, event: dict[str, Any]) -> None:
        socket = self._require_socket()
        try:
            await socket.send(json.dumps(event))
        except ConnectionClosed as exc:
            raise RealtimeDisconnected("OpenAI Realtime disconnected") from exc

    def _require_socket(self) -> RealtimeSocket:
        if self._socket is None:
            raise RealtimeDisconnected("OpenAI Realtime is not connected")
        return self._socket

    def _turn_detection(self) -> dict[str, Any]:
        common = {
            "type": self.settings.openai_realtime_vad_type,
            "create_response": True,
            "interrupt_response": True,
        }
        if self.settings.openai_realtime_vad_type == "semantic_vad":
            return {
                **common,
                "eagerness": self.settings.openai_realtime_vad_eagerness,
            }
        return {
            **common,
            "threshold": self.settings.openai_realtime_vad_threshold,
            "prefix_padding_ms": (self.settings.openai_realtime_vad_prefix_padding_ms),
            "silence_duration_ms": (
                self.settings.openai_realtime_vad_silence_duration_ms
            ),
        }
