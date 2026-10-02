"""Sync state persistence for incremental indexing.

Stores last successful sync timestamp and optional SKU registry so we only
process new/updated products and can detect deleted ones.
Supports local path or GCS (gs://bucket/key).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.utils.gcs_storage import path_exists, read_text, write_text

logger = get_logger(__name__)


def _default_state_path() -> str:
    try:
        return get_settings().SYNC_STATE_PATH
    except Exception:
        return os.getenv("SYNC_STATE_PATH", "data/sync_state.json")


def load_sync_state(state_path: str | Path | None = None) -> dict[str, Any]:
    """Load sync state from JSON file. Returns dict with last_sync_at, etc."""
    path = str(state_path) if state_path is not None else _default_state_path()
    if not path_exists(path):
        return {
            "last_sync_at": None,
            "last_successful_sync_at": None,
            "last_run_at": None,
            "indexed_skus": [],
            "run_count": 0,
            "last_error": None,
        }
    try:
        content = read_text(path)
        if not content:
            return {
                "last_sync_at": None,
                "last_successful_sync_at": None,
                "last_run_at": None,
                "indexed_skus": [],
                "run_count": 0,
                "last_error": None,
            }
        raw = json.loads(content)
        if not isinstance(raw.get("indexed_skus"), list):
            raw["indexed_skus"] = []
        return raw
    except Exception as e:
        logger.warning("Failed to load sync state from %s: %s", path, e)
        return {
            "last_sync_at": None,
            "last_successful_sync_at": None,
            "last_run_at": None,
            "indexed_skus": [],
            "run_count": 0,
            "last_error": str(e),
        }


def save_sync_state(
    state: dict[str, Any],
    state_path: str | Path | None = None,
    *,
    last_sync_at: str | None = None,
    last_successful_sync_at: str | None = None,
    indexed_skus: list[str] | None = None,
    last_error: str | None = None,
) -> None:
    """Persist sync state. Pass kwargs to update specific fields before saving."""
    path = str(state_path) if state_path is not None else _default_state_path()
    if last_sync_at is not None:
        state["last_sync_at"] = last_sync_at
    if last_successful_sync_at is not None:
        state["last_successful_sync_at"] = last_successful_sync_at
    if indexed_skus is not None:
        state["indexed_skus"] = indexed_skus
    if last_error is not None:
        state["last_error"] = last_error
    state["last_run_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    state["run_count"] = state.get("run_count", 0) + 1
    content = json.dumps(state, indent=2)
    try:
        write_text(path, content)
    except Exception as e:
        logger.exception("Failed to save sync state to %s: %s", path, e)


def get_since_timestamp(state: dict[str, Any]) -> str:
    """Return ISO timestamp to use as 'since' for incremental fetch.
    Uses last_successful_sync_at if set, else a safe old date so first run gets recent only
    or we can use a very old date to include all (e.g. 1970-01-01).
    """
    since = state.get("last_successful_sync_at") or state.get("last_sync_at")
    if since:
        return since
    # First run: use old date so we don't pull entire catalog in one incremental run.
    # Operator can run a full sync once to bootstrap, then incremental uses this.
    return "1970-01-01T00:00:00Z"
