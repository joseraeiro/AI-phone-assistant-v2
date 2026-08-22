"""Structured post-call reports generated with the OpenAI Responses API."""

import json
import logging
from asyncio import to_thread
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from fastapi import Depends
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings, get_settings
from app.db.dependencies import get_call_repository
from app.db.models import utc_now
from app.db.repository import CallRepository

logger = logging.getLogger(__name__)


class ReportItem(BaseModel):
    """One report statement with its evidence strength preserved."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2_000)
    certainty: Literal["confirmed", "uncertain", "not_obtained"]


class ImportantNumbers(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prices: list[ReportItem]
    dates: list[ReportItem]
    times: list[ReportItem]
    reference_numbers: list[ReportItem]


class PostCallReport(BaseModel):
    """Strict durable report schema returned by the summary model."""

    model_config = ConfigDict(extra="forbid")

    objective_status: Literal["success", "partial", "failed", "unknown"]
    summary: str = Field(min_length=1, max_length=5_000)
    information_obtained: list[ReportItem]
    actions_taken: list[str]
    commitments: list[str]
    follow_up: list[ReportItem]
    important_numbers: ImportantNumbers


class ResponsesResource(Protocol):
    def parse(self, **kwargs: Any) -> Any: ...


class SummaryClient(Protocol):
    responses: ResponsesResource


SUMMARY_INSTRUCTIONS = """You generate a factual post-call operational report.
Use only the supplied call data. Never invent, infer, or upgrade uncertainty.
Every information, follow-up, and important-number item must label certainty as
confirmed, uncertain, or not_obtained. If requested information was not obtained,
say so explicitly. Keep commitments empty unless the supplied authorized actions
and evidence clearly show a binding action occurred. Be concise and useful.
"""


class PostCallSummaryService:
    """Generate at most one durable report, while permitting failed retries."""

    def __init__(
        self,
        settings: Settings,
        repository: CallRepository,
        client: SummaryClient | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self._client = client

    async def generate(self, call_id: UUID | str) -> bool:
        """Claim, generate, validate, and persist a report for one completed call."""

        if not await self.repository.claim_summary(call_id):
            return False
        try:
            report = await to_thread(self._request_report, await self._input(call_id))
        except Exception as exc:
            logger.exception("POST_CALL_SUMMARY_FAILED call_id=%s", call_id)
            await self.repository.update_call(
                call_id,
                summary_status="failed",
                summary_error=f"Summary generation failed ({type(exc).__name__})",
            )
            return False
        await self.repository.update_call(
            call_id,
            summary_status="completed",
            summary_json=report.model_dump_json(),
            summary_text=report.summary,
            summary_generated_at=utc_now(),
            summary_error=None,
        )
        await self.repository.record_event(
            call_id, "SUMMARY_GENERATED", dedupe_key="summary-generated"
        )
        logger.info("POST_CALL_SUMMARY_GENERATED call_id=%s", call_id)
        return True

    def _request_report(self, input_data: dict[str, Any]) -> PostCallReport:
        client = self._client
        if client is None:
            if self.settings.openai_api_key is None:
                raise RuntimeError("OPENAI_API_KEY is required")
            client = OpenAI(
                api_key=self.settings.openai_api_key.get_secret_value()
            )
        response = client.responses.parse(
            model=self.settings.openai_summary_model,
            instructions=SUMMARY_INSTRUCTIONS,
            input=json.dumps(input_data, ensure_ascii=False),
            text_format=PostCallReport,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise ValueError("Responses API returned no parsed report")
        return PostCallReport.model_validate(parsed)

    async def _input(self, call_id: UUID | str) -> dict[str, Any]:
        call = await self.repository.get_call(call_id)
        if call is None:
            raise LookupError(str(call_id))
        events = await self.repository.events(call_id)
        tool_outcomes = []
        for event in events:
            if event.event_type == "TOOL_COMPLETED":
                tool_outcomes.append(json.loads(event.payload))
        return {
            "objective": call.objective,
            "context": call.context,
            "preferences": call.preferences,
            "constraints": call.constraints,
            "authorized_actions": json.loads(call.authorized_actions),
            "objective_status": call.objective_status,
            "final_transcript": [
                {"speaker": row.speaker, "text": row.text}
                for row in await self.repository.transcripts(call_id)
            ],
            "captured_facts": [
                {
                    "category": row.category,
                    "fact": row.fact,
                    "confidence": row.confidence,
                }
                for row in await self.repository.facts(call_id)
            ],
            "important_tool_outcomes": tool_outcomes,
        }


def get_summary_service(
    settings: Annotated[Settings, Depends(get_settings)],
    repository: Annotated[CallRepository, Depends(get_call_repository)],
) -> PostCallSummaryService:
    return PostCallSummaryService(settings, repository)
