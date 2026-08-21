import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.domain.calls import CallConfiguration
from app.main import app
from app.routers.twilio import get_realtime_session
from app.services.call_store import CallStore, get_call_store
from app.services.media_stream import (
    MalformedMediaEvent,
    MediaStreamSession,
    UnexpectedMediaFormat,
)
from tests.helpers import call_configuration

FIXTURE_DIRECTORY = Path(__file__).parent / "fixtures" / "twilio_media"
CALL_SID = "CA11111111111111111111111111111111"
STREAM_SID = "MZ11111111111111111111111111111111"
INTERNAL_CALL_ID = "12345678-1234-5678-1234-567812345678"


class WaitingRealtimeSession:
    def configure_agent(self, call: CallConfiguration) -> None:
        pass

    async def connect(self) -> None:
        pass

    async def configure(self) -> None:
        pass

    async def append_input_audio(self, payload: str) -> None:
        pass

    async def receive_event(self) -> dict[str, Any]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def close(self) -> None:
        pass


def fixture_message(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIRECTORY / f"{name}.json").read_text())


def started_session() -> MediaStreamSession:
    session = MediaStreamSession()
    session.handle_event(fixture_message("connected"))
    session.handle_event(fixture_message("start"))
    return session


def test_connected_and_start_capture_call_correlation_and_format() -> None:
    session = started_session()

    assert session.connected is True
    assert session.call_sid == CALL_SID
    assert session.stream_sid == STREAM_SID
    assert session.media_format == {
        "encoding": "audio/x-mulaw",
        "sampleRate": 8000,
        "channels": 1,
    }
    assert session.custom_parameters == {"internal_call_id": INTERNAL_CALL_ID}


def test_media_packets_and_decoded_bytes_are_counted() -> None:
    session = started_session()

    session.handle_event(fixture_message("media"))
    session.handle_event(fixture_message("media"))

    assert session.media_packets == 2
    assert session.media_bytes == 8
    assert session.approximate_audio_seconds == 0.001


def test_dtmf_mark_and_stop_events_parse_cleanly() -> None:
    session = started_session()

    assert session.handle_event(fixture_message("dtmf")) is True
    assert session.handle_event(fixture_message("mark")) is True
    assert session.handle_event(fixture_message("stop")) is False
    assert session.stopped is True


def test_unknown_event_does_not_crash_or_change_counters(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = started_session()
    caplog.set_level(logging.WARNING)

    assert session.handle_event({"event": "future-event", "value": 1}) is True

    assert session.media_packets == 0
    assert "MEDIA_STREAM_UNKNOWN_EVENT event=future-event" in caplog.text


@pytest.mark.parametrize(
    "message",
    [
        {},
        {"event": "media", "media": {"payload": "AAECAw=="}},
        {"event": "media", "media": {"payload": "not base64"}},
        {"event": "start", "start": {}},
        [],
    ],
)
def test_malformed_events_are_rejected(message: object) -> None:
    session = MediaStreamSession()

    with pytest.raises(MalformedMediaEvent):
        session.handle_event(message)


def test_unexpected_media_format_is_rejected() -> None:
    message = fixture_message("start")
    message["start"]["mediaFormat"]["sampleRate"] = 16000

    with pytest.raises(UnexpectedMediaFormat):
        MediaStreamSession().handle_event(message)


def test_websocket_handles_complete_stream_and_logs_summary(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        app_base_url="https://public.example.test",
        twilio_validate_signatures=False,
    )
    app.dependency_overrides[get_realtime_session] = WaitingRealtimeSession
    store = CallStore()
    store.add(call_configuration())
    app.dependency_overrides[get_call_store] = lambda: store
    caplog.set_level(logging.INFO)
    with (
        TestClient(app) as client,
        client.websocket_connect("/twilio/media") as websocket,
    ):
        websocket.send_text("not-json")
        websocket.send_json({"event": "future-event"})
        for event in ("connected", "start", "media", "dtmf", "mark", "stop"):
            websocket.send_json(fixture_message(event))
        assert websocket.receive()["type"] == "websocket.close"
    app.dependency_overrides.clear()

    assert "MEDIA_STREAM_CONNECTED" in caplog.text
    assert (
        f"MEDIA_STREAM_STARTED call_sid={CALL_SID} stream_sid={STREAM_SID}"
        in caplog.text
    )
    assert "MEDIA_RECEIVING" in caplog.text
    assert "packets=1 bytes=4" in caplog.text
    assert "MEDIA_STREAM_STOPPED" in caplog.text
    assert "MEDIA_STREAM_MALFORMED invalid_json" in caplog.text


def test_websocket_handles_client_disconnect_cleanly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        app_base_url="https://public.example.test",
        twilio_validate_signatures=False,
    )
    app.dependency_overrides[get_realtime_session] = WaitingRealtimeSession
    store = CallStore()
    store.add(call_configuration())
    app.dependency_overrides[get_call_store] = lambda: store
    caplog.set_level(logging.INFO)
    with (
        TestClient(app) as client,
        client.websocket_connect("/twilio/media") as websocket,
    ):
        websocket.send_json(fixture_message("connected"))
        websocket.send_json(fixture_message("start"))
    app.dependency_overrides.clear()

    assert "MEDIA_STREAM_DISCONNECTED" in caplog.text
