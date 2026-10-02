from __future__ import annotations

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_api_key_header = APIKeyHeader(name="x-mosty-api-key", auto_error=False)


async def verify_api_key(
    api_key: str | None = Security(_api_key_header),
) -> str | None:
    settings = get_settings()
    expected = settings.MOSTY_RAG_API_KEY

    if not expected:
        logger.warning("MOSTY_RAG_API_KEY not set — allowing unauthenticated access")
        return None

    if not api_key or api_key != expected:
        logger.warning(
            "auth_failed kind=mosty_api_key reason=%s",
            "missing_header" if not api_key else "invalid_key",
        )
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key. Provide header: x-mosty-api-key",
        )
    return api_key


async def verify_admin_api_key(
    api_key: str | None = Security(_api_key_header),
) -> str | None:
    """Require admin API key for /admin/* routes. Separate from normal API key.

    In local development (APP_ENV=local) with no ADMIN_API_KEY configured,
    access is granted without a key so the dashboard works out of the box.
    In production, ADMIN_API_KEY must be set and matched.
    """
    settings = get_settings()
    expected = settings.ADMIN_API_KEY

    if not expected or not expected.strip():
        if settings.APP_ENV == "local":
            return None
        logger.warning("ADMIN_API_KEY not set in production — admin endpoints disabled")
        raise HTTPException(
            status_code=403,
            detail="Admin access disabled. Set ADMIN_API_KEY to enable admin endpoints.",
        )

    if not api_key or api_key.strip() != expected.strip():
        logger.warning(
            "auth_failed kind=admin_api_key reason=%s",
            "missing_header" if not api_key else "invalid_key",
        )
        raise HTTPException(
            status_code=403,
            detail="Invalid or missing admin API key. Provide header: x-mosty-api-key with admin key.",
        )
    return api_key
