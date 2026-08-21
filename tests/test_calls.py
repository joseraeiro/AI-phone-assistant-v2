from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings, get_settings
from app.main import app
from app.routers.calls import get_call_service
from app.services.call_store import CallStore, get_call_store
from app.services.twilio import OutboundCallService
from tests.helpers import call_request


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_settings] = lambda: Settings(
        twilio_validate_signatures=False
    )
    app.dependency_overrides[get_call_store] = CallStore
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "number",
    [
        "351123456789",
        "+0123456789",
        "+1",
        "+351 123 456 789",
        "+351-123-456-789",
        "+1234567890123456",
        "not-a-number",
        "",
    ],
)
def test_rejects_non_e164_destination_numbers(client: TestClient, number: str) -> None:
    response = client.post("/calls", json=call_request(destination_number=number))

    assert response.status_code == 422


def test_accepts_e164_destination_and_creates_call(client: TestClient) -> None:
    service = Mock(spec=OutboundCallService)
    service.create.side_effect = lambda destination, internal_id: SimpleNamespace(
        sid="CA123", status="queued", internal_call_id=internal_id
    )
    app.dependency_overrides[get_call_service] = lambda: service

    response = client.post("/calls", json=call_request())

    assert response.status_code == 201
    response_body = response.json()
    assert response_body == {
        "call_sid": "CA123",
        "status": "queued",
        "simulated": False,
        "internal_call_id": response_body["internal_call_id"],
    }
    service.create.assert_called_once()
    assert service.create.call_args.args[0] == "+351211234567"
    assert isinstance(service.create.call_args.args[1], UUID)


def test_twilio_sdk_receives_expected_call_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    internal_call_id = UUID("12345678-1234-5678-1234-567812345678")
    monkeypatch.setattr("app.services.twilio.uuid4", lambda: internal_call_id)
    twilio_client = Mock()
    twilio_client.calls.create.return_value = SimpleNamespace(
        sid="CA456", status="queued"
    )
    settings = Settings(
        app_base_url="https://calls.example.test/",
        twilio_account_sid="ACtest",
        twilio_auth_token=SecretStr("secret"),
        twilio_phone_number="+351210000000",
    )

    result = OutboundCallService(settings, twilio_client).create("+351211234567")

    assert result.sid == "CA456"
    assert result.status == "queued"
    twilio_client.calls.create.assert_called_once_with(
        to="+351211234567",
        from_="+351210000000",
        url=(
            "https://calls.example.test/twilio/voice"
            "?call_id=12345678-1234-5678-1234-567812345678"
        ),
        method="POST",
        status_callback="https://calls.example.test/twilio/call-status",
        status_callback_method="POST",
        status_callback_event=["initiated", "ringing", "answered", "completed"],
    )


def test_dry_run_does_not_call_twilio() -> None:
    twilio_client = Mock()
    service = OutboundCallService(Settings(dry_run=True), twilio_client)

    result = service.create("+351211234567")

    assert result.sid == "DRY_RUN"
    assert result.status == "simulated"
    assert result.internal_call_id
    twilio_client.calls.create.assert_not_called()


def test_end_call_uses_twilio_completed_status() -> None:
    twilio_client = Mock()
    settings = Settings(
        twilio_account_sid="ACtest",
        twilio_auth_token=SecretStr("secret"),
    )

    OutboundCallService(settings, twilio_client).end("CA123")

    twilio_client.calls.assert_called_once_with("CA123")
    twilio_client.calls.return_value.update.assert_called_once_with(status="completed")


def test_dry_run_endpoint_returns_simulated_result(client: TestClient) -> None:
    app.dependency_overrides[get_call_service] = lambda: OutboundCallService(
        Settings(dry_run=True)
    )

    response = client.post("/calls", json=call_request())

    assert response.status_code == 201
    response_body = response.json()
    assert response_body == {
        "call_sid": "DRY_RUN",
        "status": "simulated",
        "simulated": True,
        "internal_call_id": response_body["internal_call_id"],
    }
    assert UUID(response_body["internal_call_id"])


def test_missing_destination_is_invalid(client: TestClient) -> None:
    response = client.post("/calls", json={})

    assert response.status_code == 422
