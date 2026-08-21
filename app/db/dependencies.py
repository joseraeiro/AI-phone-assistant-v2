"""FastAPI dependencies for durable call history."""

from typing import Annotated

from fastapi import Depends

from app.config import Settings, get_settings
from app.db.database import get_database
from app.db.repository import CallRepository


def get_call_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> CallRepository:
    """Provide a repository backed by the configured async database."""

    return CallRepository(get_database(settings))
