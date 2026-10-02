"""In-memory implementation of ConversationRepository (current behavior with TTL)."""

from __future__ import annotations

import time
import uuid
import json
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import (
    ChatSession,
    ConversationTurn,
    UserPreferences,
)
from app.repositories.conversation_repository import ConversationRepository

logger = get_logger(__name__)

MAX_HISTORY_TURNS = 40


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _context_text(content: str) -> str:
    if not content:
        return ""
    s = content.strip()
    if s.startswith("{") and '"v":1' in s and '"text"' in s:
        try:
            data = json.loads(s)
            txt = data.get("text")
            if isinstance(txt, str):
                return txt
        except Exception:
            pass
    return content


class _SessionEntry:
    __slots__ = ("session", "last_access")

    def __init__(self, session: ChatSession) -> None:
        self.session = session
        self.last_access = time.monotonic()


class InMemoryConversationRepository(ConversationRepository):
    """In-memory store with TTL expiry; same behavior as original MemoryService."""

    def __init__(self) -> None:
        self._store: dict[str, _SessionEntry] = {}
        self._shares: dict[str, dict[str, Any]] = {}
        self._lock = Lock()
        self._settings = get_settings()

    def _ttl_seconds(self) -> float:
        return self._settings.MEMORY_TTL_MINUTES * 60

    def _prune_expired(self) -> None:
        cutoff = time.monotonic() - self._ttl_seconds()
        expired = [k for k, v in self._store.items() if v.last_access < cutoff]
        for k in expired:
            del self._store[k]

    def create_session(self, language: str = "auto") -> ChatSession:
        with self._lock:
            self._prune_expired()
            session = ChatSession(
                session_id=uuid.uuid4().hex[:16],
                created_at=_now_iso(),
                updated_at=_now_iso(),
                language=language,
            )
            self._store[session.session_id] = _SessionEntry(session)
            logger.info("Session created: %s", session.session_id)
            return session

    def get_session(self, session_id: str) -> ChatSession | None:
        with self._lock:
            self._prune_expired()
            entry = self._store.get(session_id)
            if entry is None:
                return None
            entry.last_access = time.monotonic()
            return entry.session

    def get_or_create(self, session_id: str | None, language: str = "auto") -> ChatSession:
        if session_id:
            existing = self.get_session(session_id)
            if existing:
                return existing
        return self.create_session(language)

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            self._shares.pop(session_id, None)
            return self._store.pop(session_id, None) is not None

    def add_turn(self, session_id: str, role: str, content: str) -> None:
        with self._lock:
            entry = self._store.get(session_id)
            if not entry:
                return
            turn = ConversationTurn(role=role, content=content, timestamp=_now_iso())
            entry.session.conversation_history.append(turn)
            entry.session.updated_at = _now_iso()
            entry.last_access = time.monotonic()
            if len(entry.session.conversation_history) > MAX_HISTORY_TURNS:
                entry.session.conversation_history = (
                    entry.session.conversation_history[-MAX_HISTORY_TURNS:]
                )

    def get_conversation_context(self, session_id: str, max_turns: int = 10) -> str:
        with self._lock:
            entry = self._store.get(session_id)
            if not entry:
                return ""
            history = entry.session.conversation_history[-max_turns:]
            if not history:
                return ""
            lines: list[str] = []
            for turn in history:
                prefix = "User" if turn.role == "user" else "Assistant"
                content = _context_text(turn.content)[:600]
                lines.append(f"{prefix}: {content}")
            return "\n".join(lines)

    def update_session(self, session_id: str, **kwargs: Any) -> None:
        with self._lock:
            entry = self._store.get(session_id)
            if not entry:
                return
            for k, v in kwargs.items():
                if hasattr(entry.session, k) and v is not None:
                    setattr(entry.session, k, v)
            entry.session.updated_at = _now_iso()
            entry.last_access = time.monotonic()

    def add_retrieved_skus(self, session_id: str, skus: list[str]) -> None:
        with self._lock:
            entry = self._store.get(session_id)
            if not entry:
                return
            existing = set(entry.session.retrieved_skus)
            for s in skus:
                if s not in existing:
                    entry.session.retrieved_skus.append(s)
                    existing.add(s)

    def update_preferences(self, session_id: str, **kwargs: Any) -> None:
        with self._lock:
            entry = self._store.get(session_id)
            if not entry:
                return
            prefs = entry.session.user_preferences
            for k, v in kwargs.items():
                if hasattr(prefs, k) and v is not None:
                    setattr(prefs, k, v)
            entry.session.updated_at = _now_iso()

    def list_sessions(self, limit: int = 50, offset: int = 0) -> list[ChatSession]:
        with self._lock:
            self._prune_expired()
            entries = sorted(self._store.values(), key=lambda e: e.last_access, reverse=True)
            sessions = [e.session for e in entries[offset : offset + limit]]
            return sessions

    def clear_session_history(self, session_id: str) -> bool:
        with self._lock:
            entry = self._store.get(session_id)
            if not entry:
                return False
            entry.session.conversation_history.clear()
            entry.session.updated_at = _now_iso()
            return True

    def _is_share_active(self, share: dict[str, Any]) -> bool:
        if not share.get("is_active", False):
            return False
        exp = share.get("expires_at")
        if not exp:
            return True
        try:
            if isinstance(exp, str) and exp.endswith("Z"):
                exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
            else:
                exp_dt = datetime.fromisoformat(str(exp))
            return datetime.now(timezone.utc) < exp_dt
        except Exception:
            return False

    def get_session_share(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            share = self._shares.get(session_id)
            if not share:
                return None
            if not self._is_share_active(share):
                return None
            return dict(share)

    def upsert_session_share(self, session_id: str, token_hash: str, expires_at: str | None = None) -> dict[str, Any]:
        with self._lock:
            now = _now_iso()
            existing = self._shares.get(session_id)
            created = existing.get("created_at") if existing else now
            share = {
                "session_id": session_id,
                "share_token_hash": token_hash,
                "is_active": True,
                "visibility_mode": "public_readonly",
                "created_at": created,
                "updated_at": now,
                "expires_at": expires_at,
                "revoked_at": None,
                "access_count": existing.get("access_count", 0) if existing else 0,
                "last_accessed_at": existing.get("last_accessed_at") if existing else None,
            }
            self._shares[session_id] = share
            return dict(share)

    def revoke_session_share(self, session_id: str) -> bool:
        with self._lock:
            share = self._shares.get(session_id)
            if not share or not share.get("is_active"):
                return False
            share["is_active"] = False
            now = _now_iso()
            share["revoked_at"] = now
            share["updated_at"] = now
            return True

    def get_shared_session_by_token_hash(self, token_hash: str) -> tuple[ChatSession, dict[str, Any]] | None:
        with self._lock:
            for session_id, share in self._shares.items():
                if share.get("share_token_hash") != token_hash:
                    continue
                if not self._is_share_active(share):
                    return None
                entry = self._store.get(session_id)
                if not entry:
                    return None
                share["access_count"] = int(share.get("access_count", 0)) + 1
                share["last_accessed_at"] = _now_iso()
                return entry.session, dict(share)
            return None

