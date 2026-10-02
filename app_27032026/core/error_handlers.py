"""Standard API error envelope + trace id for JSON responses."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _trace_id(request: Request | None) -> str:
    if request is None:
        return uuid.uuid4().hex[:12]
    tid = getattr(request.state, "trace_id", None)
    if isinstance(tid, str) and tid:
        return tid
    tid = uuid.uuid4().hex[:12]
    request.state.trace_id = tid
    return tid


def error_payload(
    *,
    code: str,
    message: str,
    request: Request | None = None,
    status_code: int = 400,
    retry_after_ms: int | None = None,
    details: Any = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "traceId": _trace_id(request),
        },
    }
    if retry_after_ms is not None:
        body["error"]["retryAfterMs"] = retry_after_ms
    if details is not None:
        body["error"]["details"] = details
    return body


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict):
        msg = str(detail.get("message", detail))
        code = str(detail.get("code", f"HTTP_{exc.status_code}"))
    elif isinstance(detail, list):
        msg = "Request invalid"
        code = f"HTTP_{exc.status_code}"
    else:
        msg = str(detail) if detail is not None else "Error"
        code = f"HTTP_{exc.status_code}"
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(code=code, message=msg, request=request),
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=error_payload(
            code="VALIDATION_ERROR",
            message="Request validation failed",
            request=request,
            details=exc.errors(),
        ),
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    settings = get_settings()
    logger.exception("Unhandled exception trace=%s: %s", _trace_id(request), exc)
    msg = str(exc) if settings.DEBUG else "Internal server error"
    return JSONResponse(
        status_code=500,
        content=error_payload(
            code="INTERNAL_ERROR",
            message=msg,
            request=request,
        ),
    )


async def rate_limit_exception_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    retry_ms = None
    try:
        lim = getattr(exc, "limit", None)
        ra = getattr(lim, "retry_after", None) if lim is not None else None
        if ra is not None:
            retry_ms = int(float(ra) * 1000)
    except (TypeError, ValueError, AttributeError):
        retry_ms = None
    detail = getattr(exc, "detail", None)
    msg = str(detail) if detail else "Too many requests"
    payload = error_payload(
        code="RATE_LIMITED",
        message=msg,
        request=request,
        retry_after_ms=retry_ms,
    )
    resp = JSONResponse(status_code=429, content=payload)
    if retry_ms is not None:
        resp.headers["Retry-After"] = str(max(1, retry_ms // 1000))
    return resp
