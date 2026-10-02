"""Read/write JSON or text from local path or GCS (gs://bucket/key)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


def _parse_gs_path(path: str | Path) -> tuple[str, str] | None:
    """Return (bucket, key) if path is gs://bucket/key, else None."""
    s = str(path).strip()
    if s.startswith("gs://"):
        rest = s[5:]
        if "/" in rest:
            bucket, key = rest.split("/", 1)
            return bucket, key
        return rest, ""
    return None


def read_text(path: str | Path) -> str | None:
    """Read file content. Supports local path or gs://bucket/key."""
    gs = _parse_gs_path(path)
    if gs:
        try:
            from google.cloud import storage
            client = storage.Client()
            bucket = client.bucket(gs[0])
            blob = bucket.blob(gs[1])
            return blob.download_as_text(encoding="utf-8")
        except Exception as e:
            logger.warning("GCS read %s failed: %s", path, e)
            return None
    p = Path(path)
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def write_text(path: str | Path, content: str) -> bool:
    """Write content to file. Supports local path or gs://bucket/key."""
    gs = _parse_gs_path(path)
    if gs:
        try:
            from google.cloud import storage
            client = storage.Client()
            bucket = client.bucket(gs[0])
            blob = bucket.blob(gs[1])
            blob.upload_from_string(content, content_type="application/json")
            return True
        except Exception as e:
            logger.exception("GCS write %s failed: %s", path, e)
            return False
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return True


def path_exists(path: str | Path) -> bool:
    """Return True if path exists (local or GCS)."""
    gs = _parse_gs_path(path)
    if gs:
        try:
            from google.cloud import storage
            client = storage.Client()
            bucket = client.bucket(gs[0])
            blob = bucket.blob(gs[1])
            return blob.exists()
        except Exception:
            return False
    return Path(path).exists()
