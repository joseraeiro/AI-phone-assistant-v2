"""Twilio voice and lifecycle webhooks."""

import json
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request, Response, WebSocket
from fastapi.websockets import WebSocketDisconnect, WebSocketState
from twilio.twiml.voice_response import VoiceResponse

from app.agent.tools import ToolDispatcher
from app.config import Settings, get_settings
from app.db.dependencies import get_call_repository
from app.db.models import utc_now
from app.db.repository import CallRepository
from app.schemas import StatusReceived
from app.security import twilio_webhook_guard, validate_twilio_websocket
from app.services.audio_codec import PcmuPassthroughCodec
from app.services.call_history import CallHistory
from app.services.call_store import (
    CallNotFoundError,
    CallStore,
    get_call_store,
)
from app.services.media_stream import (
    MalformedMediaEvent,
    MediaStreamSession,
    UnexpectedMediaFormat,
)
from app.services.openai_realtime import (
    OpenAIRealtimeSession,
    RealtimeConfigurationError,
)
from app.services.realtime_bridge import RealtimeAudioBridge

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/twilio", tags=["twilio"])

CALL_STATUSES = frozenset(
    {
        "queued",
        "initiated",
        "ringing",
        "in-progress",
        "completed",
        "busy",
        "failed",
        "no-answer",
        "canceled",
    }
)


async def validate_twilio_request(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> None:
    """Validate the webhook unless explicitly disabled in settings."""

    await twilio_webhook_guard(settings)(request)


def get_realtime_session(
    settings: Annotated[Settings, Depends(get_settings)],
) -> OpenAIRealtimeSession:
    """Provide one unopened Realtime session for a Twilio media connection."""

    return OpenAIRealtimeSession(settings)


@router.post("/voice", dependencies=[Depends(validate_twilio_request)])
async def voice(
    call_id: Annotated[UUID, Query(description="Internal call correlation ID")],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Connect the answered call to the bidirectional Media Stream endpoint."""

    twiml = VoiceResponse()
    connect = twiml.connect()
    stream = connect.stream(url=_media_websocket_url(settings))
    stream.parameter(name="internal_call_id", value=str(call_id))
    return Response(content=str(twiml), media_type="application/xml")


def _media_websocket_url(settings: Settings) -> str:
    base_url = (settings.app_base_url or "").rstrip("/")
    if base_url.startswith("https://"):
        return f"wss://{base_url.removeprefix('https://')}/twilio/media"
    if base_url.startswith("http://"):
        return f"ws://{base_url.removeprefix('http://')}/twilio/media"
    raise ValueError("APP_BASE_URL must be an absolute HTTP(S) URL")


@router.websocket("/media")
async def media_stream(
    websocket: WebSocket,
    settings: Annotated[Settings, Depends(get_settings)],
    realtime: Annotated[OpenAIRealtimeSession, Depends(get_realtime_session)],
    store: Annotated[CallStore, Depends(get_call_store)],
    repository: Annotated[CallRepository, Depends(get_call_repository)],
) -> None:
    """Bridge one authenticated Twilio media connection to OpenAI Realtime."""

    if not validate_twilio_websocket(websocket, settings):
        logger.warning("MEDIA_STREAM_REJECTED invalid_signature")
        await websocket.close(code=1008)
        return

    await websocket.accept()
    session = MediaStreamSession()
    try:
        while True:
            raw_message = await websocket.receive_text()
            try:
                message = json.loads(raw_message)
                should_continue = session.handle_event(message)
            except json.JSONDecodeError:
                logger.warning("MEDIA_STREAM_MALFORMED invalid_json")
                continue
            except MalformedMediaEvent as exc:
                logger.warning("MEDIA_STREAM_MALFORMED reason=%s", exc)
                continue
            except UnexpectedMediaFormat as exc:
                logger.error("MEDIA_STREAM_UNSUPPORTED_FORMAT reason=%s", exc)
                await websocket.close(code=1003)
                return
            if message.get("event") == "start":
                raw_call_id = session.custom_parameters.get("internal_call_id")
                try:
                    internal_call_id = UUID(raw_call_id or "")
                    runtime = store.get(internal_call_id)
                except (ValueError, CallNotFoundError):
                    logger.error("MEDIA_STREAM_UNKNOWN_INTERNAL_CALL")
                    await websocket.close(code=1008)
                    return
                realtime.configure_agent(runtime.configuration)
                history = (
                    CallHistory(repository, internal_call_id)
                    if runtime.persistence_enabled
                    else None
                )
                if history is not None:
                    await history.update_call(
                        twilio_call_sid=session.call_sid,
                        twilio_stream_sid=session.stream_sid,
                        status="in-progress",
                        answered_at=utc_now(),
                    )
                    await history.event(
                        "STREAM_STARTED",
                        payload={"stream_sid": session.stream_sid},
                        dedupe_key=f"stream-started:{session.stream_sid}",
                    )
                bridge = RealtimeAudioBridge(
                    twilio=websocket,
                    realtime=realtime,
                    media_session=session,
                    codec=PcmuPassthroughCodec(),
                    tool_dispatcher=ToolDispatcher(runtime),
                    history=history,
                )
                try:
                    await bridge.run()
                except RealtimeConfigurationError:
                    logger.error("OPENAI_REALTIME_CONFIGURATION_ERROR")
                    await websocket.close(code=1011)
                    return
                if websocket.client_state is not WebSocketState.DISCONNECTED:
                    await websocket.close(code=1000)
                return
            if not should_continue:
                await websocket.close(code=1000)
                return
    except WebSocketDisconnect:
        logger.info(
            "MEDIA_STREAM_DISCONNECTED call_sid=%s stream_sid=%s packets=%d bytes=%d",
            session.call_sid,
            session.stream_sid,
            session.media_packets,
            session.media_bytes,
        )


@router.post(
    "/call-status",
    response_model=StatusReceived,
    dependencies=[Depends(validate_twilio_request)],
)
async def call_status(
    call_sid: Annotated[str, Form(alias="CallSid", min_length=1, max_length=64)],
    call_status: Annotated[str, Form(alias="CallStatus", min_length=1, max_length=32)],
    repository: Annotated[CallRepository, Depends(get_call_repository)],
) -> StatusReceived:
    """Acknowledge and clearly log a Twilio lifecycle callback."""

    if call_status in CALL_STATUSES:
        logger.info(
            "Twilio call lifecycle call_sid=%s status=%s", call_sid, call_status
        )
    else:
        logger.warning(
            "Twilio call lifecycle call_sid=%s unknown_status=%s",
            call_sid,
            call_status,
        )
    call = await repository.get_call_by_twilio_sid(call_sid)
    if call is not None and call_status in CALL_STATUSES:
        event_type = {
            "queued": "CALL_REQUESTED",
            "initiated": "CALL_REQUESTED",
            "ringing": "CALL_RINGING",
            "in-progress": "CALL_ANSWERED",
            "completed": "CALL_COMPLETED",
            "busy": "CALL_FAILED",
            "failed": "CALL_FAILED",
            "no-answer": "CALL_FAILED",
            "canceled": "CALL_FAILED",
        }[call_status]
        dedupe_key = event_type.lower().replace("_", "-")
        inserted = await repository.record_event(
            call.id,
            event_type,
            payload={"call_sid": call_sid, "status": call_status},
            dedupe_key=dedupe_key,
        )
        if inserted:
            updates: dict[str, object] = {"status": call_status}
            now = utc_now()
            if call_status == "in-progress":
                updates["answered_at"] = now
            if call_status in {"completed", "busy", "failed", "no-answer", "canceled"}:
                updates["ended_at"] = now
            if event_type == "CALL_FAILED":
                updates["error_message"] = f"Twilio call ended with {call_status}"
            await repository.update_call(call.id, **updates)
    return StatusReceived(call_sid=call_sid, status=call_status)
