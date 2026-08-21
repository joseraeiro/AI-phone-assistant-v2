import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from twilio.request_validator import RequestValidator

from app.config import Settings, get_settings
from app.main import app


@pytest.fixture
def unsigned_client() -> TestClient:
    app.dependency_overrides[get_settings] = lambda: Settings(
        app_base_url="https://public.example.test", twilio_validate_signatures=False
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_voice_webhook_returns_bidirectional_stream_twiml(
    unsigned_client: TestClient,
) -> None:
    response = unsigned_client.post(
        "/twilio/voice?call_id=12345678-1234-5678-1234-567812345678"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "<Connect>" in response.text
    assert '<Stream url="wss://public.example.test/twilio/media">' in response.text
    assert 'name="internal_call_id"' in response.text
    assert 'value="12345678-1234-5678-1234-567812345678"' in response.text
    assert "<Say" not in response.text


@pytest.mark.parametrize(
    "call_status",
    [
        "queued",
        "initiated",
        "ringing",
        "in-progress",
        "completed",
        "busy",
        "failed",
        "no-answer",
        "canceled",
    ],
)
def test_status_callback_handles_lifecycle_states(
    unsigned_client: TestClient,
    caplog: pytest.LogCaptureFixture,
    call_status: str,
) -> None:
    caplog.set_level(logging.INFO)

    response = unsigned_client.post(
        "/twilio/call-status",
        data={"CallSid": "CA123", "CallStatus": call_status},
    )

    assert response.status_code == 200
    assert response.json() == {
        "received": True,
        "call_sid": "CA123",
        "status": call_status,
    }
    assert f"status={call_status}" in caplog.text


def test_status_callback_rejects_missing_fields(unsigned_client: TestClient) -> None:
    response = unsigned_client.post("/twilio/call-status", data={"CallSid": "CA123"})

    assert response.status_code == 422


def test_signature_validation_rejects_invalid_signature() -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        app_base_url="https://public.example.test",
        twilio_auth_token=SecretStr("auth-token"),
        twilio_validate_signatures=True,
    )
    with TestClient(app) as client:
        response = client.post(
            "/twilio/call-status",
            data={"CallSid": "CA123", "CallStatus": "completed"},
            headers={"X-Twilio-Signature": "invalid"},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 403


def test_signature_validation_accepts_official_twilio_signature() -> None:
    token = "auth-token"
    url = "https://public.example.test/twilio/call-status"
    params = {"CallSid": "CA123", "CallStatus": "completed"}
    signature = RequestValidator(token).compute_signature(url, params)
    app.dependency_overrides[get_settings] = lambda: Settings(
        app_base_url="https://public.example.test",
        twilio_auth_token=SecretStr(token),
        twilio_validate_signatures=True,
    )
    with TestClient(app) as client:
        response = client.post(
            "/twilio/call-status",
            data=params,
            headers={"X-Twilio-Signature": signature},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 200


def test_signature_validation_fails_closed_without_auth_token() -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        twilio_validate_signatures=True
    )
    with TestClient(app) as client:
        response = client.post(
            "/twilio/voice?call_id=12345678-1234-5678-1234-567812345678"
        )
    app.dependency_overrides.clear()

    assert response.status_code == 503
