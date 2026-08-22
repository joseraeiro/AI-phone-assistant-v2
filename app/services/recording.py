"""Optional Twilio live-call recording and authenticated media retrieval."""

import logging
import re
from asyncio import to_thread
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Protocol
from uuid import UUID

import httpx
from fastapi import Depends
from twilio.rest import Client

from app.config import Settings, get_settings
from app.db.repository import CallRepository
from app.services.call_history import CallHistory

logger = logging.getLogger(__name__)


class RecordingConfigurationError(RuntimeError):
    pass


class RecordingUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class StartedRecording:
    sid: str
    status: str
    channels: int


class RecordingHttpClient(Protocol):
    def get(self, url: str, **kwargs: Any) -> Any: ...


class TwilioRecordingService:
    """Twilio calls-recordings API boundary with no policy decisions."""

    def __init__(
        self,
        settings: Settings,
        client: Any | None = None,
        http_client: RecordingHttpClient | None = None,
    ) -> None:
        self.settings = settings
        self._client = client
        self._http_client = http_client

    def start(self, call_sid: str) -> StartedRecording:
        if self.settings.dry_run:
            return StartedRecording("RE" + "0" * 32, "in-progress", 2)
        client = self._twilio_client()
        recording = client.calls(call_sid).recordings.create(
            recording_channels="dual",
            recording_track="both",
            recording_status_callback=self._callback_url(),
            recording_status_callback_method="POST",
            recording_status_callback_event=["in-progress completed absent"],
        )
        return StartedRecording(
            sid=str(recording.sid),
            status=str(recording.status or "in-progress"),
            channels=int(recording.channels or 2),
        )

    def fetch_wav(self, recording_sid: str, *, channels: int = 2) -> bytes:
        self._validate_recording_sid(recording_sid)
        account_sid, auth_token = self._credentials()
        url = (
            "https://api.twilio.com/2010-04-01/Accounts/"
            f"{account_sid}/Recordings/{recording_sid}.wav"
        )
        client = self._http_client or httpx.Client(
            auth=(account_sid, auth_token), timeout=30
        )
        close_client = self._http_client is None
        try:
            response = client.get(url, params={"RequestedChannels": channels})
            if response.status_code == 400 and channels == 2:
                response = client.get(url, params={"RequestedChannels": 1})
            if response.status_code == 404:
                raise RecordingUnavailable("Recording media is unavailable")
            response.raise_for_status()
            return bytes(response.content)
        finally:
            if close_client:
                client.close()

    def download(self, recording_sid: str, *, channels: int = 2) -> Path:
        self._validate_recording_sid(recording_sid)
        data = self.fetch_wav(recording_sid, channels=channels)
        directory = self.settings.recordings_dir.resolve()
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{recording_sid}.wav"
        temporary = directory / f".{recording_sid}.tmp"
        temporary.write_bytes(data)
        temporary.replace(destination)
        return destination

    def _twilio_client(self) -> Any:
        if self._client is not None:
            return self._client
        account_sid, auth_token = self._credentials()
        return Client(account_sid, auth_token)

    def _credentials(self) -> tuple[str, str]:
        if (
            not self.settings.twilio_account_sid
            or self.settings.twilio_auth_token is None
        ):
            raise RecordingConfigurationError("Twilio credentials are required")
        return (
            self.settings.twilio_account_sid,
            self.settings.twilio_auth_token.get_secret_value(),
        )

    def _callback_url(self) -> str:
        base_url = (self.settings.app_base_url or "").rstrip("/")
        if not base_url.startswith(("https://", "http://")):
            raise RecordingConfigurationError(
                "APP_BASE_URL must be an absolute HTTP(S) URL"
            )
        return f"{base_url}/twilio/recording-status"

    @staticmethod
    def _validate_recording_sid(recording_sid: str) -> None:
        if re.fullmatch(r"RE[A-Za-z0-9]{32}", recording_sid) is None:
            raise RecordingUnavailable("Invalid recording identifier")


class RecordingCoordinator:
    """Start once for a call and persist provider metadata and events."""

    def __init__(
        self,
        call_id: UUID,
        call_sid: str,
        repository: CallRepository,
        service: TwilioRecordingService,
    ) -> None:
        self.call_id = call_id
        self.call_sid = call_sid
        self.repository = repository
        self.service = service
        self.history = CallHistory(repository, call_id)

    async def start(self, *, consent: bool) -> dict[str, Any]:
        existing = await self.repository.recording(self.call_id)
        if existing is not None:
            return {"started": True, "recording_sid": existing.recording_sid}
        try:
            recording = await to_thread(self.service.start, self.call_sid)
        except Exception as exc:
            logger.exception("TWILIO_RECORDING_START_FAILED call_id=%s", self.call_id)
            await self.history.event(
                "RECORDING_FAILED",
                payload={"error": type(exc).__name__},
                dedupe_key="recording-start-failed",
            )
            return {"started": False, "error": "Recording is unavailable"}
        await self.repository.upsert_recording(
            self.call_id,
            recording.sid,
            status=recording.status,
            channels=recording.channels,
        )
        await self.history.event(
            "RECORDING_STARTED",
            payload={"consent_confirmed": consent},
            dedupe_key="recording-started",
        )
        return {"started": True, "recording_sid": recording.sid}


def get_recording_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> TwilioRecordingService:
    return TwilioRecordingService(settings)
