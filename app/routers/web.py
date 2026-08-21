"""Minimal server-rendered dashboard and live call detail UI."""

import asyncio
import json
from asyncio import to_thread
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from twilio.base.exceptions import TwilioRestException

from app.db.dependencies import get_call_repository
from app.db.models import Call, utc_now
from app.db.repository import CallRepository
from app.routers.calls import get_call_service
from app.services.twilio import ConfigurationError, OutboundCallService

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory="app/templates")
TERMINAL_STATUSES = {"completed", "busy", "failed", "no-answer", "canceled"}


def _duration(call: Call) -> int | None:
    start = call.answered_at or call.started_at
    end = call.ended_at
    if start is None or end is None:
        return None
    return max(0, round((end - start).total_seconds()))


def _status_label(status: str) -> str:
    return {
        "created": "CREATED",
        "queued": "CALLING",
        "initiated": "CALLING",
        "ringing": "RINGING",
        "in-progress": "LIVE",
        "completed": "COMPLETED",
        "failed": "FAILED",
        "busy": "FAILED",
        "no-answer": "NO ANSWER",
        "canceled": "CANCELED",
        "simulated": "COMPLETED",
    }.get(status, status.replace("-", " ").upper())


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    repository: Annotated[CallRepository, Depends(get_call_repository)],
) -> HTMLResponse:
    calls = await repository.recent_calls()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"calls": calls, "duration": _duration, "status_label": _status_label},
    )


@router.get("/calls/new", response_class=HTMLResponse)
async def new_call(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "new_call.html", {})


@router.get("/calls/{call_id}", response_class=HTMLResponse)
async def call_detail(
    call_id: UUID,
    request: Request,
    repository: Annotated[CallRepository, Depends(get_call_repository)],
) -> HTMLResponse:
    call = await repository.get_call(call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    return templates.TemplateResponse(
        request,
        "call_detail.html",
        {
            "call": call,
            "transcripts": await repository.transcripts(call_id),
            "facts": await repository.facts(call_id),
            "events": await repository.events(call_id),
            "duration": _duration(call),
            "status_label": _status_label(call.status),
            "is_terminal": call.status in TERMINAL_STATUSES,
        },
    )


@router.post("/calls/{call_id}/end")
async def end_call(
    call_id: UUID,
    repository: Annotated[CallRepository, Depends(get_call_repository)],
    service: Annotated[OutboundCallService, Depends(get_call_service)],
) -> JSONResponse:
    """Request provider hangup once and safely accept repeated requests."""

    call = await repository.get_call(call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    if call.status in TERMINAL_STATUSES:
        return JSONResponse({"status": call.status, "already_ended": True})
    try:
        if call.twilio_call_sid is not None:
            await to_thread(service.end, call.twilio_call_sid)
    except ConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except TwilioRestException as exc:
        raise HTTPException(
            status_code=502, detail="Twilio could not end the call"
        ) from exc
    await repository.update_call(call_id, status="completed", ended_at=utc_now())
    await repository.record_event(
        call_id, "CALL_END_REQUESTED", dedupe_key="owner-end-requested"
    )
    return JSONResponse({"status": "completed", "already_ended": False})


@router.get("/calls/{call_id}/events")
async def call_updates(
    call_id: UUID,
    request: Request,
    repository: Annotated[CallRepository, Depends(get_call_repository)],
) -> StreamingResponse:
    """Stream small historical snapshots; never send browser audio."""

    if await repository.get_call(call_id) is None:
        raise HTTPException(status_code=404, detail="Call not found")

    async def stream() -> Any:
        previous = ""
        while not await request.is_disconnected():
            snapshot = await _snapshot(repository, call_id)
            encoded = json.dumps(snapshot, ensure_ascii=False)
            if encoded != previous:
                yield f"event: call\ndata: {encoded}\n\n"
                previous = encoded
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _snapshot(repository: CallRepository, call_id: UUID) -> dict[str, Any]:
    call = await repository.get_call(call_id)
    if call is None:
        return {"missing": True}
    transcripts = await repository.transcripts(call_id)
    facts = await repository.facts(call_id)
    events = await repository.events(call_id)
    return {
        "status": call.status,
        "status_label": _status_label(call.status),
        "objective_status": call.objective_status,
        "ended_at": _iso(call.ended_at),
        "duration": _duration(call),
        "transcripts": [
            {"id": row.id, "speaker": row.speaker, "text": row.text}
            for row in transcripts
        ],
        "facts": [
            {
                "id": row.id,
                "category": row.category,
                "fact": row.fact,
                "confidence": row.confidence,
            }
            for row in facts
        ],
        "events": [
            {"id": row.id, "type": row.event_type, "created_at": _iso(row.created_at)}
            for row in events
        ],
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
