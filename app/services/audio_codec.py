"""Audio format boundary between telephony and model providers."""

import base64
import binascii
from typing import Protocol


class InvalidAudioPayload(ValueError):
    """Raised when a provider supplies malformed base64 audio."""


class AudioCodec(Protocol):
    """Translate base64 audio at provider boundaries."""

    def twilio_to_openai(self, payload: str) -> str: ...

    def openai_to_twilio(self, payload: str) -> str: ...


class PcmuPassthroughCodec:
    """Validate and directly forward compatible 8 kHz G.711 mu-law audio."""

    def twilio_to_openai(self, payload: str) -> str:
        self._validate(payload)
        return payload

    def openai_to_twilio(self, payload: str) -> str:
        self._validate(payload)
        return payload

    @staticmethod
    def _validate(payload: str) -> None:
        if not isinstance(payload, str) or not payload:
            raise InvalidAudioPayload("audio payload must be a non-empty string")
        try:
            base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise InvalidAudioPayload("audio payload is not valid base64") from exc
