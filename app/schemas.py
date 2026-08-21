"""HTTP request and response schemas."""

from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

E164Number = Annotated[
    str,
    StringConstraints(pattern=r"^\+[1-9]\d{1,14}$", min_length=3, max_length=16),
]


class CallCreate(BaseModel):
    """Minimum information needed to initiate an outbound call."""

    destination_number: E164Number


class CallCreated(BaseModel):
    """Accepted outbound call information."""

    call_sid: str
    status: str
    simulated: bool = False


class StatusReceived(BaseModel):
    """Acknowledgement for a Twilio lifecycle callback."""

    received: Literal[True] = True
    call_sid: str
    status: str
