"""Optional recording policy, Twilio API, callback, and retrieval tests."""

import asyncio
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.agent.instructions import build_agent_instructions
from app.agent.tools import ToolDispatcher, ToolDispatchError, realtime_tool_definitions
from app.config import Settings, get_settings
from app.db.repository import CallRepository
from app.domain.calls import CallConfiguration, CallRuntime
from app.main import app
from app.routers.calls import get_call_service
from app.services.call_store import CallStore, get_call_store
from app.services.recording import (
    RecordingCoordinator,
    RecordingUnavailable,
    StartedRecording,
    TwilioRecordingService,
    get_recording_service,
)
from app.services.twilio import OutboundCallService
from tests.helpers import call_configuration, call_request

CALL_SID = "CA" + "1" * 32
RECORDING_SID = "RE" + "2" * 32


def configured_call(policy: str) -> CallConfiguration:
    return call_configuration().model_copy(update={"recording_policy": policy})


def test_off_and_always_policies_do_not_offer_consent_tool() -> None:
    off = configured_call("off")
    always = configured_call("always")

    assert Settings().default_recording_policy == "ask"
    assert "Recording is disabled" in build_agent_instructions(off)
    assert "configured recording to start automatically" in build_agent_instructions(
        always
    )
    assert all(
        tool["name"] != "start_recording_after_consent"
        for tool in realtime_tool_definitions("off")
    )
    assert all(
        tool["name"] != "start_recording_after_consent"
        for tool in realtime_tool_definitions("always")
    )


def test_ask_policy_acceptance_is_validated_and_decline_does_nothing() -> None:
    runtime = CallRuntime(configured_call("ask"))
    dispatcher = ToolDispatcher(runtime)
    tool_names = {tool["name"] for tool in realtime_tool_definitions("ask")}

    assert "ask naturally for recording consent" in build_agent_instructions(
        runtime.configuration
    )
    assert "start_recording_after_consent" in tool_names
    assert runtime.recording_requested is False  # A refusal invokes no tool.
    with pytest.raises(ToolDispatchError):
        dispatcher.dispatch(
            "start_recording_after_consent", '{"consent_confirmed":false}'
        )
    result = dispatcher.dispatch(
        "start_recording_after_consent", '{"consent_confirmed":true}'
    )
    assert result == {"recording_requested": True, "consent": "confirmed"}
    assert runtime.recording_requested is True


def test_live_recording_uses_dual_channel_both_tracks() -> None:
    client = Mock()
    client.calls.return_value.recordings.create.return_value = SimpleNamespace(
        sid=RECORDING_SID, status="in-progress", channels=2
    )
    settings = Settings(
        app_base_url="https://calls.example.test",
        twilio_account_sid="ACtest",
        twilio_auth_token=SecretStr("secret"),
    )

    result = TwilioRecordingService(settings, client).start(CALL_SID)

    assert result.sid == RECORDING_SID
    client.calls.assert_called_once_with(CALL_SID)
    client.calls.return_value.recordings.create.assert_called_once_with(
        recording_channels="dual",
        recording_track="both",
        recording_status_callback="https://calls.example.test/twilio/recording-status",
        recording_status_callback_method="POST",
        recording_status_callback_event=["in-progress completed absent"],
    )


def test_accepted_and_always_start_are_idempotent(
    isolated_database: CallRepository,
) -> None:
    async def scenario() -> None:
        call = configured_call("ask")
        await isolated_database.create_call(
            call, openai_model="gpt-realtime-2.1", openai_voice="marin"
        )
        service = Mock()
        service.start.return_value = StartedRecording(
            RECORDING_SID, "in-progress", 2
        )
        coordinator = RecordingCoordinator(
            call.internal_call_id, CALL_SID, isolated_database, service
        )

        accepted = await coordinator.start(consent=True)
        repeated = await coordinator.start(consent=False)

        assert accepted["started"] is True
        assert repeated["recording_sid"] == RECORDING_SID
        service.start.assert_called_once_with(CALL_SID)

        always_call = configured_call("always").model_copy(
            update={"internal_call_id": uuid4()}
        )
        await isolated_database.create_call(
            always_call, openai_model="gpt-realtime-2.1", openai_voice="marin"
        )
        always_service = Mock()
        always_service.start.return_value = StartedRecording(
            "RE" + "3" * 32, "in-progress", 2
        )
        always_coordinator = RecordingCoordinator(
            always_call.internal_call_id,
            "CA" + "4" * 32,
            isolated_database,
            always_service,
        )

        always_result = await always_coordinator.start(consent=False)

        assert always_result["started"] is True
        always_service.start.assert_called_once_with("CA" + "4" * 32)

    asyncio.run(scenario())


@pytest.fixture
def recording_client() -> Iterator[TestClient]:
    settings = Settings(dry_run=True, twilio_validate_signatures=False)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_call_service] = lambda: OutboundCallService(settings)
    app.dependency_overrides[get_call_store] = CallStore
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_callback_is_idempotent_and_wav_is_securely_proxied(
    recording_client: TestClient, isolated_database: CallRepository
) -> None:
    created = recording_client.post("/calls", json=call_request()).json()
    call_id = created["internal_call_id"]
    asyncio.run(isolated_database.update_call(call_id, twilio_call_sid=CALL_SID))
    service = Mock(spec=TwilioRecordingService)
    service.settings = Settings()
    service.fetch_wav.return_value = b"RIFF-test-wav"
    app.dependency_overrides[get_recording_service] = lambda: service
    callback = {
        "CallSid": CALL_SID,
        "RecordingSid": RECORDING_SID,
        "RecordingStatus": "completed",
        "RecordingDuration": "12",
        "RecordingChannels": "2",
    }

    first = recording_client.post("/twilio/recording-status", data=callback)
    second = recording_client.post("/twilio/recording-status", data=callback)
    media = recording_client.get(f"/calls/{call_id}/recording.wav")

    assert first.status_code == 200
    assert second.status_code == 200
    recording = asyncio.run(isolated_database.recording(call_id))
    assert recording is not None
    assert recording.status == "completed"
    assert recording.duration == 12
    assert recording.channels == 2
    events = asyncio.run(isolated_database.events(call_id))
    recording_events = [
        event for event in events if event.event_type == "RECORDING_STATUS_CHANGED"
    ]
    assert len(recording_events) == 1
    assert media.status_code == 200
    assert media.content == b"RIFF-test-wav"
    assert media.headers["content-type"] == "audio/wav"
    assert media.headers["cache-control"] == "private, no-store"
    service.fetch_wav.assert_called_once_with(RECORDING_SID, channels=2)
    service.download.assert_not_called()
    detail = recording_client.get(f"/calls/{call_id}")
    assert f'src="/calls/{call_id}/recording.wav"' in detail.text
    assert "secret" not in detail.text


def test_recording_unavailable_returns_not_found(
    recording_client: TestClient, isolated_database: CallRepository
) -> None:
    created = recording_client.post("/calls", json=call_request()).json()
    call_id = created["internal_call_id"]
    asyncio.run(
        isolated_database.upsert_recording(
            call_id, RECORDING_SID, status="completed", channels=2
        )
    )
    service = Mock(spec=TwilioRecordingService)
    service.settings = Settings()
    service.fetch_wav.side_effect = RecordingUnavailable("unavailable")
    app.dependency_overrides[get_recording_service] = lambda: service

    response = recording_client.get(f"/calls/{call_id}/recording.wav")

    assert response.status_code == 404


def test_local_download_falls_back_to_mono(tmp_path: Path) -> None:
    unavailable_dual = SimpleNamespace(status_code=400, content=b"")
    mono = SimpleNamespace(status_code=200, content=b"RIFF-local")
    mono.raise_for_status = Mock()
    http_client = Mock()
    http_client.get.side_effect = [unavailable_dual, mono]
    settings = Settings(
        twilio_account_sid="ACtest",
        twilio_auth_token=SecretStr("secret"),
        recordings_dir=tmp_path,
    )
    service = TwilioRecordingService(settings, http_client=http_client)

    path = service.download(RECORDING_SID, channels=2)

    assert path.read_bytes() == b"RIFF-local"
    assert path.name == f"{RECORDING_SID}.wav"
    assert http_client.get.call_args_list[0].kwargs["params"] == {
        "RequestedChannels": 2
    }
    assert http_client.get.call_args_list[1].kwargs["params"] == {
        "RequestedChannels": 1
    }
