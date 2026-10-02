"""Rate limiting via slowapi, configurable through .env."""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.core.config import get_settings


def _key_func(request: Request) -> str:
    settings = get_settings()
    if not settings.RATE_LIMIT_ENABLED:
        return ""
    api_key = request.headers.get("x-mosty-api-key")
    if api_key:
        return f"key:{api_key}"
    return get_remote_address(request)


limiter = Limiter(
    key_func=_key_func,
    enabled=get_settings().RATE_LIMIT_ENABLED,
    default_limits=[get_settings().RATE_LIMIT_DEFAULT],
)


def get_default_limit() -> str:
    return get_settings().RATE_LIMIT_DEFAULT


def get_ai_limit() -> str:
    return get_settings().RATE_LIMIT_AI


def get_sync_limit() -> str:
    return get_settings().RATE_LIMIT_SYNC
