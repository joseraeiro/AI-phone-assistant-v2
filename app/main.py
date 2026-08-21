"""FastAPI application entry point."""

import logging

from fastapi import FastAPI

from app.routers import calls, twilio

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(title="Personal AI Telephone Agent", version="0.1.0")
app.include_router(calls.router)
app.include_router(twilio.router)


@app.get("/health", tags=["operations"])
async def health() -> dict[str, str]:
    """Return process liveness without contacting external providers."""

    return {"status": "ok"}
