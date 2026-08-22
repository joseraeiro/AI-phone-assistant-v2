"""Release-facing health and startup configuration checks."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.config import Settings, get_settings
from app.main import app


@pytest.fixture
def health_client() -> Iterator[TestClient]:
    settings = Settings(
        dry_run=False,
        twilio_account_sid="ACconfigured",
        twilio_auth_token=SecretStr("configured"),
        twilio_phone_number="+351210000000",
        openai_api_key=SecretStr("configured"),
    )
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_health_checks_database_without_provider_calls(
    health_client: TestClient,
) -> None:
    response = health_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "twilio_configured": True,
        "openai_configured": True,
        "dry_run": False,
    }


def test_live_configuration_reports_missing_variable_names_only() -> None:
    assert Settings().missing_live_configuration() == [
        "APP_BASE_URL",
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_PHONE_NUMBER",
        "OPENAI_API_KEY",
    ]
    assert Settings(dry_run=True).missing_live_configuration() == []


def test_invalid_public_base_url_fails_early() -> None:
    with pytest.raises(ValidationError, match="APP_BASE_URL must start"):
        Settings(app_base_url="public.example.test")
