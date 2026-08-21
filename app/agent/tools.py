"""Allowlisted, Pydantic-validated internal tool dispatch."""

import json
from collections.abc import Callable
from enum import StrEnum
from typing import Any, Literal

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


class StartRecordingArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consent_confirmed: Literal[True]


TOOL_ARGUMENT_MODELS: dict[str, type[BaseModel]] = {
    "save_fact": SaveFactArguments,
    "set_objective_status": SetObjectiveStatusArguments,
    "finish_call": FinishCallArguments,
}


def realtime_tool_definitions(
    recording_policy: Literal["off", "ask", "always"] = "off",
) -> list[dict[str, Any]]:
    """Return base tools plus the consent tool only when policy permits it."""

    descriptions = {
        "save_fact": "Save one important fact stated or confirmed during the call.",
        "set_objective_status": (
            "Set operational objective status with a short explicit reason."
        ),
        "finish_call": (
            "Request a graceful call ending after a brief spoken thank-you and goodbye."
        ),
    }
    models = dict(TOOL_ARGUMENT_MODELS)
    if recording_policy == "ask":
        models["start_recording_after_consent"] = StartRecordingArguments
        descriptions["start_recording_after_consent"] = (
            "Start Twilio recording only after the remote person clearly consents."
        )
    return [
        {
            "type": "function",
            "name": name,
            "description": descriptions[name],
            "parameters": model.model_json_schema(),
        }
        for name, model in models.items()
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
        if runtime.configuration.recording_policy == "ask":
            self._handlers["start_recording_after_consent"] = self._start_recording

    def dispatch(self, name: str, arguments: str) -> dict[str, Any]:
        """Validate JSON and invoke an allowlisted handler; never dynamic-dispatch."""

        model = (
            StartRecordingArguments
            if name == "start_recording_after_consent"
            else TOOL_ARGUMENT_MODELS.get(name)
        )
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

    def _start_recording(self, arguments: BaseModel) -> dict[str, Any]:
        StartRecordingArguments.model_validate(arguments)
        self.runtime.recording_requested = True
        return {"recording_requested": True, "consent": "confirmed"}


def serialize_tool_result(result: dict[str, Any]) -> str:
    """Produce the JSON string expected by function_call_output."""

    return json.dumps(result, ensure_ascii=False)
