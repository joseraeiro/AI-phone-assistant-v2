"""HTTP request and response schemas."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, StringConstraints

from app.domain.calls import AuthorizedAction

E164Number = Annotated[
    str,
    StringConstraints(pattern=r"^\+[1-9]\d{1,14}$", min_length=3, max_length=16),
]


class CallCreate(BaseModel):
    """Owner-supplied objective, context, and authority for one call."""

    destination_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    destination_number: E164Number
    objective: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5_000)
    ]
    context: Annotated[str, StringConstraints(max_length=10_000)]
    preferences: Annotated[str, StringConstraints(max_length=5_000)]
    constraints: Annotated[str, StringConstraints(max_length=5_000)]
    language: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=2, max_length=35)
    ] = "pt-PT"
    authorized_actions: frozenset[AuthorizedAction] = frozenset()


class CallCreated(BaseModel):
    """Accepted outbound call information."""

    call_sid: str
    status: str
    simulated: bool = False
    internal_call_id: UUID


class StatusReceived(BaseModel):
    """Acknowledgement for a Twilio lifecycle callback."""

    received: Literal[True] = True
    call_sid: str
    status: str
