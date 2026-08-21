"""Important server-rendered Phase 7 routes."""

from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.routers.calls import get_call_service
from app.services.call_store import CallStore, get_call_store
from app.services.twilio import OutboundCallService


@pytest.fixture
def web_client() -> Iterator[TestClient]:
    settings = Settings(
        dry_run=True,
        twilio_validate_signatures=False,
        openai_realtime_model="gpt-realtime-2.1",
        openai_realtime_voice="marin",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_call_service] = lambda: OutboundCallService(settings)
    app.dependency_overrides[get_call_store] = CallStore
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def create_call(client: TestClient) -> str:
    response = client.post(
        "/calls",
        json={
            "destination_name": "Loja <script>alert(1)</script>",
            "destination_number": "+351211234567",
            "objective": "Confirmar o horário",
            "context": "Perguntar pelo sábado",
            "preferences": "Resposta breve",
            "constraints": "Não reservar",
            "language": "pt-PT",
            "realtime_model": "gpt-realtime-2.1",
            "voice": "marin",
        },
    )
    assert response.status_code == 201
    return response.json()["internal_call_id"]


def test_dashboard_new_call_and_detail_templates(web_client: TestClient) -> None:
    empty_dashboard = web_client.get("/")
    new_call = web_client.get("/calls/new")

    assert empty_dashboard.status_code == 200
    assert "No calls yet" in empty_dashboard.text
    assert new_call.status_code == 200
    for field in (
        "destination_name",
        "destination_number",
        "objective",
        "context",
        "preferences",
        "constraints",
        "language",
        "realtime_model",
        "voice",
    ):
        assert f'name="{field}"' in new_call.text

    call_id = create_call(web_client)
    assert UUID(call_id)
    dashboard = web_client.get("/")
    detail = web_client.get(f"/calls/{call_id}")

    assert dashboard.status_code == 200
    assert f'/calls/{call_id}' in dashboard.text
    assert "Loja &lt;script&gt;alert(1)&lt;/script&gt;" in dashboard.text
    assert "Loja <script>" not in dashboard.text
    assert detail.status_code == 200
    assert "Summary will be available in a later phase." in detail.text
    assert "Transcript" in detail.text
    assert "Facts" in detail.text
    assert "Events" in detail.text
    assert "Configuration" in detail.text
    assert f'window.callId = "{call_id}"' in detail.text


def test_end_call_is_idempotent(web_client: TestClient) -> None:
    call_id = create_call(web_client)

    first = web_client.post(f"/calls/{call_id}/end")
    second = web_client.post(f"/calls/{call_id}/end")

    assert first.status_code == 200
    assert first.json() == {"status": "completed", "already_ended": False}
    assert second.status_code == 200
    assert second.json() == {"status": "completed", "already_ended": True}
    assert "COMPLETED" in web_client.get(f"/calls/{call_id}").text


def test_unknown_call_detail_returns_not_found(web_client: TestClient) -> None:
    response = web_client.get("/calls/12345678-1234-5678-1234-567812345678")

    assert response.status_code == 404
