"""Twilio REST operations isolated behind an application service."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from twilio.rest import Client

from app.config import Settings


class CallsResource(Protocol):
    """Subset of the Twilio calls resource used by this phase."""

    def create(self, **kwargs: object) -> object: ...


class TwilioClient(Protocol):
    """Subset of the Twilio client used by this phase."""

    calls: CallsResource


@dataclass(frozen=True)
class CreatedCall:
    """Provider-independent result returned by the outbound call service."""

    sid: str
    status: str
    internal_call_id: UUID


class ConfigurationError(RuntimeError):
    """Raised when configuration required for a real call is absent."""


class OutboundCallService:
    """Create real or deliberately simulated outbound calls."""

    def __init__(self, settings: Settings, client: TwilioClient | None = None) -> None:
        self.settings = settings
        self._client = client

    def create(
        self, destination_number: str, internal_call_id: UUID | None = None
    ) -> CreatedCall:
        """Create one call, or return a deterministic dry-run result."""

        internal_call_id = internal_call_id or uuid4()
        if self.settings.dry_run:
            return CreatedCall(
                sid="DRY_RUN",
                status="simulated",
                internal_call_id=internal_call_id,
            )

        base_url = self._validated_base_url()
        account_sid = self.settings.twilio_account_sid
        auth_token = self.settings.twilio_auth_token
        from_number = self.settings.twilio_phone_number
        if not account_sid or auth_token is None or not from_number:
            raise ConfigurationError(
                "Twilio credentials and TWILIO_PHONE_NUMBER are required"
            )

        client = self._client or Client(account_sid, auth_token.get_secret_value())
        call = client.calls.create(
            to=destination_number,
            from_=from_number,
            url=f"{base_url}/twilio/voice?call_id={internal_call_id}",
            method="POST",
            status_callback=f"{base_url}/twilio/call-status",
            status_callback_method="POST",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
        )
        return CreatedCall(
            sid=str(call.sid),
            status=str(call.status or "queued"),
            internal_call_id=internal_call_id,
        )

    def _validated_base_url(self) -> str:
        base_url = (self.settings.app_base_url or "").rstrip("/")
        if not base_url.startswith(("https://", "http://")):
            raise ConfigurationError("APP_BASE_URL must be an absolute HTTP(S) URL")
        return base_url
