"""Allowlisted, Pydantic-validated internal tool dispatch."""

import json
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.calls import CallRuntime, ObjectiveStatus, SavedFact


class FactConfidence(StrEnum):
    """How strongly the remote party established a saved fact."""

    CONFIRMED = "confirmed"
    REPORTED = "reported"
    UNCERTAIN = "uncertain"


class SaveFactArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1, max_length=80)
    fact: str = Field(min_length=1, max_length=1_000)
    confidence: FactConfidence


class SetObjectiveStatusArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ObjectiveStatus
    reason: str = Field(min_length=1, max_length=500)


class FinishCallArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


TOOL_ARGUMENT_MODELS: dict[str, type[BaseModel]] = {
    "save_fact": SaveFactArguments,
    "set_objective_status": SetObjectiveStatusArguments,
    "finish_call": FinishCallArguments,
}


def realtime_tool_definitions() -> list[dict[str, Any]]:
    """Return only the three Phase 5 function schemas exposed to the model."""

    descriptions = {
        "save_fact": "Save one important fact stated or confirmed during the call.",
        "set_objective_status": (
            "Set operational objective status with a short explicit reason."
        ),
        "finish_call": (
            "Request a graceful call ending after a brief spoken thank-you and goodbye."
        ),
    }
    return [
        {
            "type": "function",
            "name": name,
            "description": descriptions[name],
            "parameters": model.model_json_schema(),
        }
        for name, model in TOOL_ARGUMENT_MODELS.items()
    ]


class ToolDispatchError(ValueError):
    """Safe error returned for unknown tools or invalid arguments."""


class ToolDispatcher:
    """Dispatch exactly the allowlisted tools against one call runtime."""

    def __init__(self, runtime: CallRuntime) -> None:
        self.runtime = runtime
        self._handlers: dict[str, Callable[[BaseModel], dict[str, Any]]] = {
            "save_fact": self._save_fact,
            "set_objective_status": self._set_objective_status,
            "finish_call": self._finish_call,
        }

    def dispatch(self, name: str, arguments: str) -> dict[str, Any]:
        """Validate JSON and invoke an allowlisted handler; never dynamic-dispatch."""

        model = TOOL_ARGUMENT_MODELS.get(name)
        handler = self._handlers.get(name)
        if model is None or handler is None:
            raise ToolDispatchError(f"Tool is not allowed: {name}")
        try:
            parsed = model.model_validate_json(arguments)
        except ValidationError as exc:
            raise ToolDispatchError(f"Invalid arguments for {name}") from exc
        return handler(parsed)

    def _save_fact(self, arguments: BaseModel) -> dict[str, Any]:
        validated = SaveFactArguments.model_validate(arguments)
        fact = SavedFact(
            category=validated.category,
            fact=validated.fact,
            confidence=validated.confidence.value,
        )
        self.runtime.facts.append(fact)
        return {"saved": True, "fact": fact.model_dump()}

    def _set_objective_status(self, arguments: BaseModel) -> dict[str, Any]:
        validated = SetObjectiveStatusArguments.model_validate(arguments)
        self.runtime.objective_status = validated.status
        self.runtime.objective_status_reason = validated.reason
        return {
            "updated": True,
            "status": validated.status.value,
            "reason": validated.reason,
        }

    def _finish_call(self, arguments: BaseModel) -> dict[str, Any]:
        validated = FinishCallArguments.model_validate(arguments)
        self.runtime.finish_requested = True
        self.runtime.finish_reason = validated.reason
        return {
            "finish_scheduled": True,
            "reason": validated.reason,
            "instruction": "Say a brief natural thank-you and goodbye now.",
        }


def serialize_tool_result(result: dict[str, Any]) -> str:
    """Produce the JSON string expected by function_call_output."""

    return json.dumps(result, ensure_ascii=False)
