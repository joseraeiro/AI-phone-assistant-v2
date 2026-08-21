"""Twilio Media Stream event parsing and per-connection state."""

import base64
import binascii
import logging
from dataclasses import dataclass, field
from typing import Any, Final

logger = logging.getLogger(__name__)

EXPECTED_MEDIA_FORMAT: Final = {
    "encoding": "audio/x-mulaw",
    "sampleRate": 8000,
    "channels": 1,
}
LOG_PACKET_INTERVAL: Final = 50


class MalformedMediaEvent(ValueError):
    """Raised when a known Media Stream event has an invalid shape."""


class UnexpectedMediaFormat(ValueError):
    """Raised when Twilio announces an unsupported audio format."""


def _mapping(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MalformedMediaEvent(f"{field_name} must be an object")
    return value


def _non_empty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise MalformedMediaEvent(f"{field_name} must be a non-empty string")
    return value


@dataclass
class MediaStreamSession:
    """Ephemeral metadata and counters owned by one Twilio WebSocket."""

    connected: bool = False
    call_sid: str | None = None
    stream_sid: str | None = None
    media_format: dict[str, Any] | None = None
    custom_parameters: dict[str, str] = field(default_factory=dict)
    media_packets: int = 0
    media_bytes: int = 0
    stopped: bool = False

    @property
    def approximate_audio_seconds(self) -> float:
        """Return duration for 8-bit, 8 kHz mu-law audio."""

        return self.media_bytes / 8000

    def handle_event(self, message: object) -> bool:
        """Apply one decoded event; return false when the stream has stopped."""

        event_message = _mapping(message, "message")
        event = _non_empty_string(event_message.get("event"), "event")
        handlers = {
            "connected": self._handle_connected,
            "start": self._handle_start,
            "media": self._handle_media,
            "dtmf": self._handle_dtmf,
            "mark": self._handle_mark,
            "stop": self._handle_stop,
        }
        handler = handlers.get(event)
        if handler is None:
            logger.warning("MEDIA_STREAM_UNKNOWN_EVENT event=%s", event)
            return True
        handler(event_message)
        return event != "stop"

    def _handle_connected(self, message: dict[str, Any]) -> None:
        _non_empty_string(message.get("protocol"), "protocol")
        _non_empty_string(message.get("version"), "version")
        self.connected = True
        logger.info("MEDIA_STREAM_CONNECTED")

    def _handle_start(self, message: dict[str, Any]) -> None:
        start = _mapping(message.get("start"), "start")
        stream_sid = _non_empty_string(
            start.get("streamSid") or message.get("streamSid"), "start.streamSid"
        )
        call_sid = _non_empty_string(start.get("callSid"), "start.callSid")
        media_format = _mapping(start.get("mediaFormat"), "start.mediaFormat")
        normalized_format = {
            "encoding": media_format.get("encoding"),
            "sampleRate": media_format.get("sampleRate"),
            "channels": media_format.get("channels"),
        }
        if normalized_format != EXPECTED_MEDIA_FORMAT:
            raise UnexpectedMediaFormat(
                f"expected {EXPECTED_MEDIA_FORMAT}, received {normalized_format}"
            )

        raw_parameters = _mapping(
            start.get("customParameters", {}), "start.customParameters"
        )
        parameters: dict[str, str] = {}
        for name, value in raw_parameters.items():
            if not isinstance(name, str) or not isinstance(value, str):
                raise MalformedMediaEvent("custom parameters must contain strings")
            parameters[name] = value

        self.call_sid = call_sid
        self.stream_sid = stream_sid
        self.media_format = normalized_format
        self.custom_parameters = parameters
        logger.info(
            "MEDIA_STREAM_STARTED call_sid=%s stream_sid=%s internal_call_id=%s",
            self.call_sid,
            self.stream_sid,
            self.custom_parameters.get("internal_call_id", "missing"),
        )

    def _handle_media(self, message: dict[str, Any]) -> None:
        if self.stream_sid is None:
            raise MalformedMediaEvent("media received before start")
        media = _mapping(message.get("media"), "media")
        payload = _non_empty_string(media.get("payload"), "media.payload")
        try:
            decoded_size = len(base64.b64decode(payload, validate=True))
        except (binascii.Error, ValueError) as exc:
            raise MalformedMediaEvent("media.payload is not valid base64") from exc

        self.media_packets += 1
        self.media_bytes += decoded_size
        if self.media_packets == 1 or self.media_packets % LOG_PACKET_INTERVAL == 0:
            logger.info(
                "MEDIA_RECEIVING call_sid=%s stream_sid=%s packets=%d bytes=%d "
                "approx_seconds=%.3f",
                self.call_sid,
                self.stream_sid,
                self.media_packets,
                self.media_bytes,
                self.approximate_audio_seconds,
            )

    def _handle_dtmf(self, message: dict[str, Any]) -> None:
        dtmf = _mapping(message.get("dtmf"), "dtmf")
        digit = _non_empty_string(dtmf.get("digit"), "dtmf.digit")
        logger.info(
            "MEDIA_DTMF_RECEIVED call_sid=%s stream_sid=%s digit=%s",
            self.call_sid,
            self.stream_sid,
            digit,
        )

    def _handle_mark(self, message: dict[str, Any]) -> None:
        mark = _mapping(message.get("mark"), "mark")
        name = _non_empty_string(mark.get("name"), "mark.name")
        logger.debug(
            "MEDIA_MARK_RECEIVED call_sid=%s stream_sid=%s name=%s",
            self.call_sid,
            self.stream_sid,
            name,
        )

    def _handle_stop(self, message: dict[str, Any]) -> None:
        _mapping(message.get("stop"), "stop")
        self.stopped = True
        logger.info(
            "MEDIA_STREAM_STOPPED call_sid=%s stream_sid=%s packets=%d bytes=%d "
            "approx_seconds=%.3f",
            self.call_sid,
            self.stream_sid,
            self.media_packets,
            self.media_bytes,
            self.approximate_audio_seconds,
        )
