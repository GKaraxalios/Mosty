"""Small in-memory attachment store for chat uploads (dev / single-instance)."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from threading import Lock

from app.core.config import get_settings


@dataclass
class StoredAttachment:
    data: bytes
    mime: str
    filename: str
    created: float


class AttachmentService:
    def __init__(self) -> None:
        self._lock = Lock()
        self._store: dict[str, StoredAttachment] = {}
        self._settings = get_settings()

    def _ttl(self) -> float:
        return float(self._settings.CHAT_ATTACHMENT_TTL_SECONDS)

    def _prune(self) -> None:
        cutoff = time.monotonic() - self._ttl()
        dead = [k for k, v in self._store.items() if v.created < cutoff]
        for k in dead:
            self._store.pop(k, None)

    def save(self, data: bytes, mime: str, filename: str) -> str:
        max_bytes = int(self._settings.CHAT_ATTACHMENT_MAX_BYTES)
        if len(data) > max_bytes:
            raise ValueError(f"File too large (max {max_bytes} bytes)")
        aid = uuid.uuid4().hex
        with self._lock:
            self._prune()
            self._store[aid] = StoredAttachment(
                data=data,
                mime=mime or "application/octet-stream",
                filename=filename or "file",
                created=time.monotonic(),
            )
        return aid

    def get(self, attachment_id: str) -> StoredAttachment | None:
        with self._lock:
            self._prune()
            ent = self._store.get(attachment_id)
            return ent

    def describe(self, ids: list[str]) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        with self._lock:
            self._prune()
            for i in ids:
                ent = self._store.get(i)
                if ent:
                    out.append({"id": i, "filename": ent.filename, "mime": ent.mime})
        return out


_attachment_svc: AttachmentService | None = None


def get_attachment_service() -> AttachmentService:
    global _attachment_svc
    if _attachment_svc is None:
        _attachment_svc = AttachmentService()
    return _attachment_svc
