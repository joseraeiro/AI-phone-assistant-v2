"""Bridge runtime events to authoritative durable call history."""

from datetime import datetime
from typing import Any
from uuid import UUID

from app.db.repository import CallRepository


class CallHistory:
    """Persist meaningful history for one call without retaining live sockets."""

    def __init__(self, repository: CallRepository, call_id: UUID) -> None:
        self.repository = repository
        self.call_id = call_id

    async def event(
        self,
        event_type: str,
        *,
        payload: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> bool:
        return await self.repository.record_event(
            self.call_id, event_type, payload=payload, dedupe_key=dedupe_key
        )

    async def transcript(
        self,
        *,
        speaker: str,
        text: str,
        source: str,
        sequence: int,
        timestamp: datetime | None = None,
    ) -> None:
        if text.strip():
            await self.repository.append_transcript(
                self.call_id,
                speaker=speaker,
                text=text,
                source=source,
                sequence=sequence,
                timestamp=timestamp,
            )

    async def update_call(self, **values: Any) -> None:
        await self.repository.update_call(self.call_id, **values)

    async def fact(self, *, category: str, fact: str, confidence: str) -> None:
        await self.repository.capture_fact(
            self.call_id, category=category, fact=fact, confidence=confidence
        )
