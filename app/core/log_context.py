"""Request-scoped context for logging (correlation id).

Set by ``RequestLoggingMiddleware``; read by formatters/filters on the root logger.
Safe for async: uses ``contextvars``.
"""
from __future__ import annotations

import contextvars
import uuid

_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mosty_request_id", default=None
)


def get_request_id() -> str | None:
    return _request_id_var.get()


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def bind_request_id(request_id: str) -> contextvars.Token:
    return _request_id_var.set(request_id)


def reset_request_id(token: contextvars.Token) -> None:
    _request_id_var.reset(token)
