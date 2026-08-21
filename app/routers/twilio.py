"""Twilio voice and lifecycle webhooks."""

import json
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request, Response, WebSocket
from fastapi.websockets import WebSocketDisconnect, WebSocketState
from twilio.twiml.voice_response import VoiceResponse

from app.config import Settings, get_settings
from app.schemas import StatusReceived
from app.security import twilio_webhook_guard, validate_twilio_websocket
from app.services.audio_codec import PcmuPassthroughCodec
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
                bridge = RealtimeAudioBridge(
                    twilio=websocket,
                    realtime=realtime,
                    media_session=session,
                    codec=PcmuPassthroughCodec(),
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
    return StatusReceived(call_sid=call_sid, status=call_status)
