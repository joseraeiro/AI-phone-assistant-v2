"""FastAPI application entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.db.database import get_database
from app.routers import calls, twilio

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create the Phase 6 schema before accepting requests."""

    database = get_database(get_settings())
    await database.create_schema()
    yield


app = FastAPI(
    title="Personal AI Telephone Agent", version="0.1.0", lifespan=lifespan
)
app.include_router(calls.router)
app.include_router(twilio.router)


@app.get("/health", tags=["operations"])
async def health() -> dict[str, str]:
    """Return process liveness without contacting external providers."""

    return {"status": "ok"}
