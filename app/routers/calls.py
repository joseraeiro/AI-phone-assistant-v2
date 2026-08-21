"""Owner-facing outbound call endpoint."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from twilio.base.exceptions import TwilioRestException

from app.config import Settings, get_settings
from app.schemas import CallCreate, CallCreated
from app.services.twilio import ConfigurationError, OutboundCallService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/calls", tags=["calls"])


def get_call_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> OutboundCallService:
    """Provide the outbound call service."""

    return OutboundCallService(settings)


@router.post("", response_model=CallCreated, status_code=status.HTTP_201_CREATED)
def create_call(
    request: CallCreate,
    service: Annotated[OutboundCallService, Depends(get_call_service)],
) -> CallCreated:
    """Validate and initiate a single outbound telephone call."""

    try:
        call = service.create(request.destination_number)
    except ConfigurationError as exc:
        logger.error("Outbound call configuration is incomplete")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except TwilioRestException as exc:
        logger.error("Twilio rejected outbound call creation (code=%s)", exc.code)
        raise HTTPException(
            status_code=502, detail="Twilio could not create the outbound call"
        ) from exc

    simulated = call.sid == "DRY_RUN"
    logger.info(
        "Outbound call accepted call_sid=%s status=%s simulated=%s",
        call.sid,
        call.status,
        simulated,
    )
    return CallCreated(
        call_sid=call.sid,
        status=call.status,
        simulated=simulated,
        internal_call_id=call.internal_call_id,
    )
