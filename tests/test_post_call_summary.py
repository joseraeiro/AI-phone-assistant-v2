"""Structured Responses API post-call report tests."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from app.config import Settings
from app.db.database import Database
from app.db.repository import CallRepository
from app.services.post_call_summary import PostCallSummaryService
from tests.helpers import call_configuration


def report(*, certainty: str = "confirmed") -> dict[str, object]:
    return {
        "objective_status": "partial",
        "summary": "Foi obtida informação parcial sobre o horário.",
        "information_obtained": [
            {"text": "A loja poderá abrir às nove.", "certainty": certainty}
        ],
        "actions_taken": ["Foi solicitado o horário."],
        "commitments": [],
        "follow_up": [
            {"text": "Confirmar o horário amanhã.", "certainty": "not_obtained"}
        ],
        "important_numbers": {
            "prices": [],
            "dates": [],
            "times": [
                {"text": "09:00", "certainty": certainty},
            ],
            "reference_numbers": [],
        },
    }


async def prepared_repository(path: Path) -> tuple[Database, CallRepository, str]:
    database = Database(f"sqlite+aiosqlite:///{path}")
    await database.create_schema()
    repository = CallRepository(database)
    call = call_configuration()
    await repository.create_call(
        call, openai_model="gpt-realtime-2.1", openai_voice="marin"
    )
    await repository.update_call(
        call.internal_call_id, status="completed", objective_status="partial"
    )
    await repository.append_transcript(
        call.internal_call_id,
        speaker="remote",
        text="Acho que abrimos às nove, mas não tenho a certeza.",
        source="openai_realtime",
    )
    await repository.capture_fact(
        call.internal_call_id,
        category="time",
        fact="Possível abertura às 09:00",
        confidence="uncertain",
    )
    await repository.record_event(
        call.internal_call_id,
        "TOOL_COMPLETED",
        payload={"name": "save_fact", "ok": True},
    )
    return database, repository, str(call.internal_call_id)


def test_successful_summary_is_structured_uncertain_and_idempotent(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database, repository, call_id = await prepared_repository(tmp_path / "ok.db")
        responses = Mock()
        responses.parse.return_value = SimpleNamespace(
            output_parsed=report(certainty="uncertain")
        )
        client = SimpleNamespace(responses=responses)
        service = PostCallSummaryService(
            Settings(openai_summary_model="gpt-5.6-luna"), repository, client
        )

        assert await service.generate(call_id) is True
        assert await service.generate(call_id) is False

        row = await repository.get_call(call_id)
        assert row is not None
        saved = json.loads(row.summary_json)
        assert row.status == "completed"
        assert row.summary_status == "completed"
        assert row.summary_generated_at is not None
        assert saved["information_obtained"][0]["certainty"] == "uncertain"
        assert saved["important_numbers"]["times"][0]["certainty"] == "uncertain"
        assert saved["commitments"] == []
        assert responses.parse.call_count == 1
        request = responses.parse.call_args.kwargs
        assert request["model"] == "gpt-5.6-luna"
        assert request["text_format"].__name__ == "PostCallReport"
        input_data = json.loads(request["input"])
        assert input_data["objective"]
        assert input_data["final_transcript"][0]["speaker"] == "remote"
        assert input_data["captured_facts"][0]["confidence"] == "uncertain"
        assert input_data["important_tool_outcomes"][0]["name"] == "save_fact"
        await database.dispose()

    asyncio.run(scenario())


def test_malformed_output_and_api_failure_preserve_call_and_allow_retry(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database, repository, call_id = await prepared_repository(tmp_path / "fail.db")
        malformed = SimpleNamespace(responses=Mock())
        malformed.responses.parse.return_value = SimpleNamespace(
            output_parsed={"objective_status": "success"}
        )
        service = PostCallSummaryService(Settings(), repository, malformed)

        assert await service.generate(call_id) is False
        failed = await repository.get_call(call_id)
        assert failed is not None
        assert failed.status == "completed"
        assert failed.summary_status == "failed"
        assert "ValidationError" in failed.summary_error

        failing = SimpleNamespace(responses=Mock())
        failing.responses.parse.side_effect = RuntimeError("provider unavailable")
        assert await PostCallSummaryService(
            Settings(), repository, failing
        ).generate(call_id) is False
        api_failed = await repository.get_call(call_id)
        assert api_failed is not None and api_failed.status == "completed"
        assert "RuntimeError" in api_failed.summary_error

        recovered = SimpleNamespace(responses=Mock())
        recovered.responses.parse.return_value = SimpleNamespace(output_parsed=report())
        assert await PostCallSummaryService(
            Settings(), repository, recovered
        ).generate(call_id) is True
        complete = await repository.get_call(call_id)
        assert complete is not None and complete.summary_status == "completed"
        await database.dispose()

    asyncio.run(scenario())
