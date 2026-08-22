"""Environment-backed application configuration."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration loaded from environment variables and an optional `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    app_base_url: str | None = None
    app_host: str = "0.0.0.0"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_timezone: str = "Europe/Lisbon"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: str = "sqlite+aiosqlite:///./ai_phone_assistant.db"
    default_recording_policy: Literal["off", "ask", "always"] = "ask"
    download_recordings_locally: bool = False
    recordings_dir: Path = Path("./data/recordings")

    twilio_account_sid: str | None = None
    twilio_auth_token: SecretStr | None = None
    twilio_phone_number: str | None = None
    twilio_validate_signatures: bool = True

    dry_run: bool = False

    openai_api_key: SecretStr | None = None
    openai_realtime_model: str = "gpt-realtime-2.1"
    openai_realtime_voice: str = "marin"
    openai_transcription_model: str = "gpt-live-transcribe"
    openai_summary_model: str = "gpt-5.6-luna"
    openai_realtime_vad_type: Literal["semantic_vad", "server_vad"] = "semantic_vad"
    openai_realtime_vad_eagerness: Literal["low", "medium", "high", "auto"] = "auto"
    openai_realtime_vad_threshold: float = Field(default=0.5, ge=0, le=1)
    openai_realtime_vad_prefix_padding_ms: int = Field(default=300, ge=0)
    openai_realtime_vad_silence_duration_ms: int = Field(default=700, ge=100)

    @field_validator("app_base_url")
    @classmethod
    def validate_app_base_url(cls, value: str | None) -> str | None:
        """Fail early for callback bases that cannot produce HTTP/WSS URLs."""

        if value is not None and not value.startswith(("https://", "http://")):
            raise ValueError("APP_BASE_URL must start with https:// or http://")
        return value.rstrip("/") if value else value

    def missing_live_configuration(self) -> list[str]:
        """Return names only; never include credential values in diagnostics."""

        if self.dry_run:
            return []
        required = {
            "APP_BASE_URL": self.app_base_url,
            "TWILIO_ACCOUNT_SID": self.twilio_account_sid,
            "TWILIO_AUTH_TOKEN": self.twilio_auth_token,
            "TWILIO_PHONE_NUMBER": self.twilio_phone_number,
            "OPENAI_API_KEY": self.openai_api_key,
        }
        return [name for name, value in required.items() if value is None]


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
