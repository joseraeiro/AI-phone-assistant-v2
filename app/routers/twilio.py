"""Twilio voice and lifecycle webhooks."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response
from twilio.twiml.voice_response import VoiceResponse

from app.config import Settings, get_settings
from app.schemas import StatusReceived
from app.security import twilio_webhook_guard

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


@router.post("/voice", dependencies=[Depends(validate_twilio_request)])
async def voice() -> Response:
    """Return the fixed Phase 1 test message and let Twilio end the call."""

    twiml = VoiceResponse()
    twiml.say(
        "Olá. Esta é uma chamada de teste do assistente virtual.",
        language="pt-PT",
    )
    return Response(content=str(twiml), media_type="application/xml")


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
