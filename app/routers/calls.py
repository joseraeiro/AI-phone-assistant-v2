"""Owner-facing outbound call endpoint."""

import logging
from asyncio import to_thread
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from twilio.base.exceptions import TwilioRestException

from app.config import Settings, get_settings
from app.db.dependencies import get_call_repository
from app.db.models import utc_now
from app.db.repository import CallRepository
from app.domain.calls import CallConfiguration
from app.schemas import CallCreate, CallCreated
from app.services.call_store import CallStore, get_call_store
from app.services.twilio import ConfigurationError, OutboundCallService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/calls", tags=["calls"])


def get_call_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> OutboundCallService:
    """Provide the outbound call service."""

    return OutboundCallService(settings)


@router.post("", response_model=CallCreated, status_code=status.HTTP_201_CREATED)
async def create_call(
    request: CallCreate,
    service: Annotated[OutboundCallService, Depends(get_call_service)],
    store: Annotated[CallStore, Depends(get_call_store)],
    repository: Annotated[CallRepository, Depends(get_call_repository)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CallCreated:
    """Validate and initiate a single outbound telephone call."""

    internal_call_id = uuid4()
    call_data = request.model_dump()
    call_data["recording_policy"] = (
        request.recording_policy or settings.default_recording_policy
    )
    configuration = CallConfiguration(
        internal_call_id=internal_call_id,
        **call_data,
    )
    runtime = store.add(configuration)
    await repository.create_call(
        configuration,
        openai_model=settings.openai_realtime_model,
        openai_voice=settings.openai_realtime_voice,
    )
    runtime.persistence_enabled = True
    await repository.record_event(
        internal_call_id, "CALL_CREATED", dedupe_key="call-created"
    )
    try:
        call = await to_thread(
            service.create, request.destination_number, internal_call_id
        )
    except ConfigurationError as exc:
        store.remove(internal_call_id)
        await repository.update_call(
            internal_call_id,
            status="failed",
            ended_at=utc_now(),
            error_message=str(exc),
        )
        await repository.record_event(
            internal_call_id, "CALL_FAILED", dedupe_key="call-failed"
        )
        logger.error("Outbound call configuration is incomplete")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except TwilioRestException as exc:
        store.remove(internal_call_id)
        await repository.update_call(
            internal_call_id,
            status="failed",
            ended_at=utc_now(),
            error_message="Twilio could not create the outbound call",
        )
        await repository.record_event(
            internal_call_id,
            "CALL_FAILED",
            payload={"twilio_code": exc.code},
            dedupe_key="call-failed",
        )
        logger.error("Twilio rejected outbound call creation (code=%s)", exc.code)
        raise HTTPException(
            status_code=502,
            detail=(
                "Twilio rejected the outbound call request. Check the destination "
                "number and Twilio Console call logs."
            ),
        ) from exc

    simulated = call.sid == "DRY_RUN"
    await repository.update_call(
        internal_call_id,
        status=call.status,
        twilio_call_sid=None if simulated else call.sid,
        started_at=utc_now(),
    )
    await repository.record_event(
        internal_call_id,
        "CALL_REQUESTED",
        payload={"call_sid": call.sid, "simulated": simulated},
        dedupe_key="call-requested",
    )
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
