"""Important server-rendered Phase 7 and report routes."""

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.db.repository import CallRepository
from app.main import app
from app.routers.calls import get_call_service
from app.services.call_store import CallStore, get_call_store
from app.services.post_call_summary import get_summary_service
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
        "recording_policy",
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
    assert "The report will be generated after the call." in detail.text
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


def test_generated_report_renders_on_detail_page(
    web_client: TestClient, isolated_database: CallRepository
) -> None:
    call_id = create_call(web_client)
    report = (
        '{"objective_status":"success","summary":"Horário confirmado.",'
        '"information_obtained":[{"text":"Fecha às 18:00",'
        '"certainty":"confirmed"}],"actions_taken":["Consultou o horário"],'
        '"commitments":[],"follow_up":[],"important_numbers":{'
        '"prices":[],"dates":[],"times":[],"reference_numbers":[]}}'
    )
    asyncio.run(
        isolated_database.update_call(
            call_id,
            status="completed",
            summary_status="completed",
            summary_json=report,
            summary_text="Horário confirmado.",
        )
    )

    detail = web_client.get(f"/calls/{call_id}")

    assert detail.status_code == 200
    assert "Horário confirmado." in detail.text
    assert "Fecha às 18:00" in detail.text
    assert "Consultou o horário" in detail.text


def test_duplicate_completed_callbacks_schedule_one_report(
    web_client: TestClient, isolated_database: CallRepository
) -> None:
    call_id = create_call(web_client)
    asyncio.run(
        isolated_database.update_call(call_id, twilio_call_sid="CA-summary-test")
    )
    summary = SimpleNamespace(generate=AsyncMock(return_value=True))
    app.dependency_overrides[get_summary_service] = lambda: summary

    first = web_client.post(
        "/twilio/call-status",
        data={"CallSid": "CA-summary-test", "CallStatus": "completed"},
    )
    second = web_client.post(
        "/twilio/call-status",
        data={"CallSid": "CA-summary-test", "CallStatus": "completed"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    summary.generate.assert_awaited_once_with(call_id)


def test_failed_report_exposes_retry_control(
    web_client: TestClient, isolated_database: CallRepository
) -> None:
    call_id = create_call(web_client)
    asyncio.run(
        isolated_database.update_call(
            call_id,
            status="completed",
            summary_status="failed",
            summary_error="Summary generation failed (RuntimeError)",
        )
    )

    detail = web_client.get(f"/calls/{call_id}")

    assert detail.status_code == 200
    assert "Summary generation failed (RuntimeError)" in detail.text
    assert 'id="retry-summary"' in detail.text
