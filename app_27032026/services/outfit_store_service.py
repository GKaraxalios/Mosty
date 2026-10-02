"""In-memory persistence for Outfit Builder saved outfits (MVP).

Replace with DB + user ids in production if needed.
"""
from __future__ import annotations

import secrets
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_lock = threading.Lock()
_outfits: dict[str, dict[str, Any]] = {}
_share_to_id: dict[str, str] = {}


def save_outfit(payload: dict[str, Any]) -> dict[str, Any]:
    """Store outfit; returns record with outfit_id, share_token, share_url fragment."""
    oid = uuid.uuid4().hex
    token = secrets.token_urlsafe(18)
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "outfit_id": oid,
        "share_token": token,
        "title": payload.get("title") or "My outfit",
        "slots": payload.get("slots") or {},
        "context": payload.get("context") or {},
        "created_at": now,
    }
    with _lock:
        _outfits[oid] = record
        _share_to_id[token] = oid
    logger.info("outfit saved id=%s", oid)
    return record


def get_outfit(outfit_id: str) -> dict[str, Any] | None:
    with _lock:
        r = _outfits.get(outfit_id)
        return dict(r) if r else None


def get_by_share_token(token: str) -> dict[str, Any] | None:
    with _lock:
        oid = _share_to_id.get(token)
        if not oid:
            return None
        r = _outfits.get(oid)
        return dict(r) if r else None
