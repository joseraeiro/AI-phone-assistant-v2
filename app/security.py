"""Twilio webhook authentication."""

from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request, WebSocket, status
from twilio.request_validator import RequestValidator

from app.config import Settings

WebhookGuard = Callable[[Request], Awaitable[None]]


def twilio_webhook_guard(settings: Settings) -> WebhookGuard:
    """Build a request guard using Twilio's official signature validator."""

    async def validate(request: Request) -> None:
        if not settings.twilio_validate_signatures:
            return

        if settings.twilio_auth_token is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Twilio signature validation is not configured",
            )

        signature = request.headers.get("X-Twilio-Signature", "")
        form = await request.form()
        public_url = _public_request_url(request, settings)
        validator = RequestValidator(settings.twilio_auth_token.get_secret_value())
        if not validator.validate(public_url, dict(form), signature):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid Twilio signature",
            )

    return validate


def _public_request_url(request: Request, settings: Settings) -> str:
    """Reconstruct the exact public callback URL used in signature generation."""

    if settings.app_base_url:
        url = f"{settings.app_base_url.rstrip('/')}{request.url.path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"
        return url
    return str(request.url)


def validate_twilio_websocket(websocket: WebSocket, settings: Settings) -> bool:
    """Validate Twilio's signature on the initial Media Stream upgrade."""

    if not settings.twilio_validate_signatures:
        return True
    if settings.twilio_auth_token is None:
        return False

    signature = websocket.headers.get("X-Twilio-Signature", "")
    public_url = _public_websocket_url(websocket, settings)
    validator = RequestValidator(settings.twilio_auth_token.get_secret_value())
    return validator.validate(public_url, {}, signature)


def _public_websocket_url(websocket: WebSocket, settings: Settings) -> str:
    if settings.app_base_url:
        base_url = settings.app_base_url.rstrip("/")
        if base_url.startswith("https://"):
            base_url = f"wss://{base_url.removeprefix('https://')}"
        elif base_url.startswith("http://"):
            base_url = f"ws://{base_url.removeprefix('http://')}"
        url = f"{base_url}{websocket.url.path}"
        if websocket.url.query:
            url = f"{url}?{websocket.url.query}"
        return url
    return str(websocket.url)
