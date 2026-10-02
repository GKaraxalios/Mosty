"""Conversation persistence: abstract interface and factory.

The rest of the app depends only on this interface; storage can be in-memory or MySQL.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.models.schemas import ChatSession


class ConversationRepository(ABC):
    """Abstract interface for chat session and message persistence."""

    @abstractmethod
    def create_session(self, language: str = "auto") -> ChatSession:
        """Create a new session; returns session with new session_id."""
        ...

    @abstractmethod
    def get_session(self, session_id: str) -> ChatSession | None:
        """Return session by id or None if not found / expired."""
        ...

    @abstractmethod
    def get_or_create(self, session_id: str | None, language: str = "auto") -> ChatSession:
        """Return existing session if session_id given and found, else create new."""
        ...

    @abstractmethod
    def delete_session(self, session_id: str) -> bool:
        """Remove session and its messages. Returns True if existed."""
        ...

    @abstractmethod
    def add_turn(self, session_id: str, role: str, content: str) -> None:
        """Append a message (user or assistant) to the session. No-op if session missing."""
        ...

    @abstractmethod
    def get_conversation_context(self, session_id: str, max_turns: int = 10) -> str:
        """Build text summary of recent turns for prompt injection."""
        ...

    @abstractmethod
    def update_session(self, session_id: str, **kwargs: Any) -> None:
        """Update session fields (e.g. last_query, last_answer, language)."""
        ...

    @abstractmethod
    def add_retrieved_skus(self, session_id: str, skus: list[str]) -> None:
        """Append SKUs to session's retrieved_skus without duplicates."""
        ...

    @abstractmethod
    def update_preferences(self, session_id: str, **kwargs: Any) -> None:
        """Update user_preferences for the session."""
        ...

    def list_sessions(self, limit: int = 50, offset: int = 0) -> list[ChatSession]:
        """List sessions (newest first). Optional; in-memory may return []."""
        return []

    def clear_session_history(self, session_id: str) -> bool:
        """Remove all messages for the session; keep session. Returns True if session existed."""
        return False

    def get_session_share(self, session_id: str) -> dict[str, Any] | None:
        """Return active share metadata for a session."""
        return None

    def upsert_session_share(self, session_id: str, token_hash: str, expires_at: str | None = None) -> dict[str, Any]:
        """Create or replace active share metadata for a session."""
        raise NotImplementedError

    def revoke_session_share(self, session_id: str) -> bool:
        """Revoke active share for a session."""
        return False

    def get_shared_session_by_token_hash(self, token_hash: str) -> tuple[ChatSession, dict[str, Any]] | None:
        """Resolve token hash to a shared session and share metadata."""
        return None



def get_conversation_repository() -> ConversationRepository:
    """Factory: returns the configured repository (memory or mysql)."""
    from app.core.config import get_settings

    settings = get_settings()
    backend = (settings.MEMORY_BACKEND or "memory").strip().lower()
    if backend == "mysql":
        from app.repositories.mysql_repository import MySQLConversationRepository
        return MySQLConversationRepository()
    from app.repositories.memory_repository import InMemoryConversationRepository
    return InMemoryConversationRepository()
