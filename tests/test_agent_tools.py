import json

import pytest

from app.agent.tools import (
    TOOL_ARGUMENT_MODELS,
    ToolDispatcher,
    ToolDispatchError,
    realtime_tool_definitions,
)
from app.domain.calls import CallRuntime, ObjectiveStatus
from tests.helpers import call_configuration


def dispatcher() -> ToolDispatcher:
    return ToolDispatcher(CallRuntime(call_configuration()))


def test_only_requested_tools_are_exposed_and_allowlisted() -> None:
    definitions = realtime_tool_definitions()

    assert set(TOOL_ARGUMENT_MODELS) == {
        "save_fact",
        "set_objective_status",
        "finish_call",
    }
    assert {definition["name"] for definition in definitions} == set(
        TOOL_ARGUMENT_MODELS
    )
    assert all(definition["type"] == "function" for definition in definitions)


def test_save_fact_persists_validated_fact_in_call_runtime() -> None:
    tools = dispatcher()

    result = tools.dispatch(
        "save_fact",
        json.dumps(
            {
                "category": "price",
                "fact": "Echeveria elegans costs €6.90",
                "confidence": "confirmed",
            }
        ),
    )

    assert result["saved"] is True
    assert tools.runtime.facts[0].fact == "Echeveria elegans costs €6.90"
    assert tools.runtime.facts[0].confidence == "confirmed"


@pytest.mark.parametrize("status", ["success", "partial", "failed", "unknown"])
def test_set_objective_status_accepts_only_documented_states(status: str) -> None:
    tools = dispatcher()

    result = tools.dispatch(
        "set_objective_status",
        json.dumps({"status": status, "reason": "Operational outcome"}),
    )

    assert result["status"] == status
    assert tools.runtime.objective_status == ObjectiveStatus(status)
    assert tools.runtime.objective_status_reason == "Operational outcome"


def test_finish_call_schedules_graceful_ending() -> None:
    tools = dispatcher()

    result = tools.dispatch(
        "finish_call", json.dumps({"reason": "Objective completed"})
    )

    assert result["finish_scheduled"] is True
    assert tools.runtime.finish_requested is True
    assert tools.runtime.finish_reason == "Objective completed"


def test_unknown_tool_and_invalid_arguments_are_rejected() -> None:
    tools = dispatcher()

    with pytest.raises(ToolDispatchError, match="not allowed"):
        tools.dispatch("run_arbitrary_python", "{}")
    with pytest.raises(ToolDispatchError, match="Invalid arguments"):
        tools.dispatch(
            "save_fact",
            json.dumps(
                {
                    "category": "price",
                    "fact": "€6.90",
                    "confidence": "invented",
                    "command": "rm -rf /",
                }
            ),
        )
