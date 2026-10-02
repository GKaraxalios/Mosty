"""Log each HTTP request: method, path, status, duration, correlation id.

Does not log request bodies, query strings, or headers (avoids secrets).
"""
from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.log_context import bind_request_id, new_request_id, reset_request_id
from app.core.logging import get_logger

logger = get_logger("app.http")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        token = bind_request_id(new_request_id())
        path = request.url.path
        method = request.method
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - t0) * 1000
            status = response.status_code
            log_fn = logger.warning if status >= 400 else logger.info
            log_fn(
                "request method=%s path=%s status=%s duration_ms=%.1f",
                method,
                path,
                status,
                duration_ms,
            )
            return response
        except Exception:
            duration_ms = (time.perf_counter() - t0) * 1000
            logger.exception(
                "request_failed method=%s path=%s duration_ms=%.1f",
                method,
                path,
                duration_ms,
            )
            raise
        finally:
            reset_request_id(token)
