"""In-memory implementation of ConversationRepository (current behavior with TTL)."""

from __future__ import annotations

import time
import uuid
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


class _SessionEntry:
    __slots__ = ("session", "last_access")

    def __init__(self, session: ChatSession) -> None:
        self.session = session
        self.last_access = time.monotonic()


class InMemoryConversationRepository(ConversationRepository):
    """In-memory store with TTL expiry; same behavior as original MemoryService."""

    def __init__(self) -> None:
        self._store: dict[str, _SessionEntry] = {}
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
                content = turn.content[:600]
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
