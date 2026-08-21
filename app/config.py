"""Environment-backed application configuration."""

from functools import lru_cache

from pydantic import Field, SecretStr
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

    twilio_account_sid: str | None = None
    twilio_auth_token: SecretStr | None = None
    twilio_phone_number: str | None = None
    twilio_validate_signatures: bool = True

    dry_run: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
