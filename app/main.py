"""FastAPI application entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import Settings, get_settings
from app.db.database import get_database
from app.routers import calls, twilio, web

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Initialize storage and report actionable live-configuration gaps."""

    current_settings = get_settings()
    database = get_database(current_settings)
    await database.create_schema()
    missing = current_settings.missing_live_configuration()
    if missing:
        logger.warning(
            "LIVE_CONFIGURATION_INCOMPLETE missing=%s; "
            "use DRY_RUN=true for UI-only testing",
            ",".join(missing),
        )
    elif current_settings.dry_run:
        logger.info("DRY_RUN_ENABLED provider calls are simulated")
    yield


app = FastAPI(
    title="Personal AI Telephone Agent", version="1.0.0", lifespan=lifespan
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(calls.router)
app.include_router(twilio.router)
app.include_router(web.router)


@app.get("/health", tags=["operations"])
async def health(
    current_settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str | bool]:
    """Check local storage and configuration presence without provider calls."""

    await get_database(current_settings).check()
    return {
        "status": "ok",
        "database": "ok",
        "twilio_configured": all(
            (
                current_settings.twilio_account_sid,
                current_settings.twilio_auth_token,
                current_settings.twilio_phone_number,
            )
        ),
        "openai_configured": current_settings.openai_api_key is not None,
        "dry_run": current_settings.dry_run,
    }
