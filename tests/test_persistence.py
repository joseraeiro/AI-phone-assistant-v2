"""Durable Phase 6 repository behavior."""

import asyncio
from datetime import UTC
from pathlib import Path
from uuid import uuid4

from app.db.database import Database
from app.db.models import utc_now
from app.db.repository import CallRepository
from app.domain.calls import CallConfiguration


def configuration() -> CallConfiguration:
    return CallConfiguration(
        internal_call_id=uuid4(),
        destination_name="Loja",
        destination_number="+351211234567",
        objective="Confirmar horários",
        context="Chamada de informação",
        preferences="Respostas concisas",
        constraints="Não reservar",
        language="pt-PT",
    )


def test_history_order_idempotency_and_restart_durability(tmp_path: Path) -> None:
    path = tmp_path / "durable.db"
    url = f"sqlite+aiosqlite:///{path}"
    call = configuration()

    async def write_history() -> None:
        database = Database(url)
        await database.create_schema()
        repository = CallRepository(database)
        row = await repository.create_call(
            call, openai_model="gpt-realtime-2.1", openai_voice="marin"
        )
        assert row.created_at.tzinfo is UTC
        await repository.update_call(
            call.internal_call_id,
            status="completed",
            twilio_call_sid="CA123",
            twilio_stream_sid="MZ123",
            started_at=utc_now(),
            answered_at=utc_now(),
            ended_at=utc_now(),
            summary_json='{"summary":"Horário confirmado"}',
            summary_text="Horário confirmado",
            summary_generated_at=utc_now(),
            summary_status="completed",
        )
        assert await repository.record_event(
            call.internal_call_id, "CALL_COMPLETED", dedupe_key="completed"
        )
        assert not await repository.record_event(
            call.internal_call_id, "CALL_COMPLETED", dedupe_key="completed"
        )
        # Completion callbacks may arrive out of order; canonical sequence wins.
        await repository.append_transcript(
            call.internal_call_id,
            sequence=2,
            speaker="agent",
            text="Fecham às 18 horas.",
            source="openai_realtime",
        )
        await repository.append_transcript(
            call.internal_call_id,
            sequence=1,
            speaker="remote",
            text="Amanhã abrimos às nove.",
            source="openai_realtime",
        )
        await repository.capture_fact(
            call.internal_call_id,
            category="schedule",
            fact="Amanhã abre às 09:00",
            confidence="confirmed",
        )
        await database.dispose()

    async def read_after_restart() -> None:
        database = Database(url)
        repository = CallRepository(database)
        row = await repository.get_call(call.internal_call_id)
        assert row is not None
        assert row.destination_name == "Loja"
        assert row.objective == "Confirmar horários"
        assert row.status == "completed"
        assert row.ended_at is not None and row.ended_at.tzinfo is UTC
        assert row.summary_text == "Horário confirmado"
        assert row.summary_generated_at is not None
        assert row.summary_generated_at.tzinfo is UTC
        assert [entry.sequence for entry in await repository.transcripts(row.id)] == [
            1,
            2,
        ]
        assert len(await repository.events(row.id)) == 1
        assert (await repository.facts(row.id))[0].fact == "Amanhã abre às 09:00"
        await database.dispose()

    asyncio.run(write_history())
    asyncio.run(read_after_restart())
