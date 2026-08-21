"""Validated call configuration and ephemeral objective state."""

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class AuthorizedAction(StrEnum):
    """Binding actions that may be explicitly granted for one call."""

    MAKE_RESERVATION = "make_reservation"
    PLACE_ORDER = "place_order"
    MAKE_PURCHASE = "make_purchase"
    SCHEDULE_APPOINTMENT = "schedule_appointment"
    ACCEPT_QUOTE = "accept_quote"
    COMMIT_MONEY = "commit_money"
    CANCEL_SERVICE = "cancel_service"
    MODIFY_SERVICE = "modify_service"
    ENTER_AGREEMENT = "enter_agreement"


class ObjectiveStatus(StrEnum):
    """Operational assessment recorded by the telephone agent."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    UNKNOWN = "unknown"


class CallConfiguration(BaseModel):
    """Owner-supplied scope and authority for a single outbound call."""

    internal_call_id: UUID
    destination_name: NonEmptyText
    destination_number: str
    objective: NonEmptyText
    context: str = Field(default="", max_length=10_000)
    preferences: str = Field(default="", max_length=5_000)
    constraints: str = Field(default="", max_length=5_000)
    language: NonEmptyText = "pt-PT"
    authorized_actions: frozenset[AuthorizedAction] = frozenset()
    realtime_model: str | None = Field(default=None, max_length=100)
    voice: str | None = Field(default=None, max_length=100)


class SavedFact(BaseModel):
    """One important fact captured during the current call."""

    category: str
    fact: str
    confidence: str


class CallRuntime:
    """Minimal process-local state needed by Phase 5 tools."""

    def __init__(self, configuration: CallConfiguration) -> None:
        self.configuration = configuration
        self.facts: list[SavedFact] = []
        self.objective_status = ObjectiveStatus.UNKNOWN
        self.objective_status_reason = "Not assessed yet"
        self.finish_requested = False
        self.finish_reason: str | None = None
        self.persistence_enabled = False
