"""Short async transactions for authoritative historical call data."""

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.db.database import Database
from app.db.models import (
    Call,
    CallEvent,
    CallRecording,
    CapturedFact,
    TranscriptEntry,
    utc_now,
)
from app.domain.calls import CallConfiguration


class CallRepository:
    """Persist calls, canonical transcripts, facts, and meaningful events."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def create_call(
        self, call: CallConfiguration, *, openai_model: str, openai_voice: str
    ) -> Call:
        row = Call(
            id=str(call.internal_call_id),
            destination_name=call.destination_name,
            destination_number=call.destination_number,
            objective=call.objective,
            context=call.context,
            preferences=call.preferences,
            constraints=call.constraints,
            language=call.language,
            authorized_actions=json.dumps(sorted(call.authorized_actions)),
            recording_policy=call.recording_policy,
            status="created",
            objective_status="unknown",
            openai_model=call.realtime_model or openai_model,
            openai_voice=call.voice or openai_voice,
        )
        async with self.database.session() as session:
            session.add(row)
            await session.commit()
        return row

    async def get_call(self, call_id: UUID | str) -> Call | None:
        async with self.database.session() as session:
            return await session.get(Call, str(call_id))

    async def get_call_by_twilio_sid(self, call_sid: str) -> Call | None:
        async with self.database.session() as session:
            return await session.scalar(
                select(Call).where(Call.twilio_call_sid == call_sid)
            )

    async def update_call(self, call_id: UUID | str, **values: Any) -> Call:
        allowed = {
            "status",
            "objective_status",
            "objective_status_reason",
            "twilio_call_sid",
            "twilio_stream_sid",
            "started_at",
            "answered_at",
            "ended_at",
            "error_message",
            "summary_json",
            "summary_text",
            "summary_generated_at",
            "summary_error",
            "summary_status",
        }
        if not values.keys() <= allowed:
            raise ValueError("Unsupported call update")
        async with self.database.session() as session:
            row = await session.get(Call, str(call_id))
            if row is None:
                raise LookupError(str(call_id))
            for name, value in values.items():
                setattr(row, name, value)
            await session.commit()
            return row

    async def record_event(
        self,
        call_id: UUID | str,
        event_type: str,
        *,
        payload: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
        created_at: datetime | None = None,
    ) -> bool:
        row = CallEvent(
            call_id=str(call_id),
            event_type=event_type,
            payload=json.dumps(payload or {}, ensure_ascii=False),
            dedupe_key=dedupe_key,
            created_at=created_at or utc_now(),
        )
        async with self.database.session() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return False
        return True

    async def append_transcript(
        self,
        call_id: UUID | str,
        *,
        speaker: str,
        text: str,
        source: str,
        sequence: int | None = None,
        timestamp: datetime | None = None,
    ) -> TranscriptEntry:
        async with self.database.session() as session:
            if sequence is None:
                maximum = await session.scalar(
                    select(func.max(TranscriptEntry.sequence)).where(
                        TranscriptEntry.call_id == str(call_id)
                    )
                )
                sequence = (maximum or 0) + 1
            row = TranscriptEntry(
                call_id=str(call_id),
                sequence=sequence,
                timestamp=timestamp or utc_now(),
                speaker=speaker,
                text=text.strip(),
                source=source,
            )
            session.add(row)
            await session.commit()
            return row

    async def capture_fact(
        self,
        call_id: UUID | str,
        *,
        category: str,
        fact: str,
        confidence: str,
    ) -> CapturedFact:
        row = CapturedFact(
            call_id=str(call_id),
            category=category,
            fact=fact,
            confidence=confidence,
        )
        async with self.database.session() as session:
            session.add(row)
            await session.commit()
        return row

    async def transcripts(self, call_id: UUID | str) -> list[TranscriptEntry]:
        async with self.database.session() as session:
            return list(
                await session.scalars(
                    select(TranscriptEntry)
                    .where(TranscriptEntry.call_id == str(call_id))
                    .order_by(TranscriptEntry.sequence)
                )
            )

    async def events(self, call_id: UUID | str) -> list[CallEvent]:
        async with self.database.session() as session:
            return list(
                await session.scalars(
                    select(CallEvent)
                    .where(CallEvent.call_id == str(call_id))
                    .order_by(CallEvent.created_at, CallEvent.id)
                )
            )

    async def facts(self, call_id: UUID | str) -> list[CapturedFact]:
        async with self.database.session() as session:
            return list(
                await session.scalars(
                    select(CapturedFact)
                    .where(CapturedFact.call_id == str(call_id))
                    .order_by(CapturedFact.created_at, CapturedFact.id)
                )
            )

    async def recent_calls(self, *, limit: int = 50) -> list[Call]:
        """Return newest calls for the dashboard."""

        async with self.database.session() as session:
            return list(
                await session.scalars(
                    select(Call).order_by(Call.created_at.desc()).limit(limit)
                )
            )

    async def claim_summary(self, call_id: UUID | str) -> bool:
        """Atomically claim an absent or failed report for generation."""

        async with self.database.session() as session:
            result = await session.execute(
                update(Call)
                .where(
                    Call.id == str(call_id),
                    Call.summary_status.in_(("pending", "failed")),
                )
                .values(summary_status="generating", summary_error=None)
            )
            await session.commit()
            return bool(result.rowcount)

    async def upsert_recording(
        self,
        call_id: UUID | str,
        recording_sid: str,
        *,
        status: str,
        duration: int | None = None,
        channels: int | None = None,
        local_path: str | None = None,
    ) -> CallRecording:
        """Create or update callback metadata without duplicate rows."""

        async with self.database.session() as session:
            row = await session.get(CallRecording, recording_sid)
            if row is None:
                row = CallRecording(
                    recording_sid=recording_sid,
                    call_id=str(call_id),
                    status=status,
                    duration=duration,
                    channels=channels,
                    local_path=local_path,
                )
                session.add(row)
            else:
                row.status = status
                if duration is not None:
                    row.duration = duration
                if channels is not None:
                    row.channels = channels
                if local_path is not None:
                    row.local_path = local_path
            await session.commit()
            return row

    async def recording(self, call_id: UUID | str) -> CallRecording | None:
        async with self.database.session() as session:
            return await session.scalar(
                select(CallRecording)
                .where(CallRecording.call_id == str(call_id))
                .order_by(CallRecording.created_at.desc())
                .limit(1)
            )

    async def recording_by_sid(self, recording_sid: str) -> CallRecording | None:
        async with self.database.session() as session:
            return await session.get(CallRecording, recording_sid)
