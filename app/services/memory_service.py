"""Session-based conversational memory.

Delegates to ConversationRepository (in-memory or MySQL based on config).
API unchanged for backward compatibility.
"""
from __future__ import annotations

from typing import Any

from app.models.schemas import ChatSession
from app.repositories.conversation_repository import (
    ConversationRepository,
    get_conversation_repository,
)


class MemoryService:
    """Facade over conversation persistence. Same API as before."""

    def __init__(self, repository: ConversationRepository | None = None) -> None:
        self._repo = repository or get_conversation_repository()

    def create_session(self, language: str = "auto") -> ChatSession:
        return self._repo.create_session(language=language)

    def get_session(self, session_id: str) -> ChatSession | None:
        return self._repo.get_session(session_id)

    def get_or_create(self, session_id: str | None, language: str = "auto") -> ChatSession:
        return self._repo.get_or_create(session_id=session_id, language=language)

    def delete_session(self, session_id: str) -> bool:
        return self._repo.delete_session(session_id)

    def add_turn(self, session_id: str, role: str, content: str) -> None:
        self._repo.add_turn(session_id, role, content)

    def get_conversation_context(self, session_id: str, max_turns: int = 10) -> str:
        return self._repo.get_conversation_context(session_id, max_turns=max_turns)

    def update_session(self, session_id: str, **kwargs: Any) -> None:
        self._repo.update_session(session_id, **kwargs)

    def add_retrieved_skus(self, session_id: str, skus: list[str]) -> None:
        self._repo.add_retrieved_skus(session_id, skus)

    def update_preferences(self, session_id: str, **kwargs: Any) -> None:
        self._repo.update_preferences(session_id, **kwargs)

    def list_sessions(self, limit: int = 50, offset: int = 0) -> list[ChatSession]:
        return self._repo.list_sessions(limit=limit, offset=offset)

    def clear_session_history(self, session_id: str) -> bool:
        return self._repo.clear_session_history(session_id)


_memory_svc: MemoryService | None = None


def get_memory_service() -> MemoryService:
    global _memory_svc
    if _memory_svc is None:
        _memory_svc = MemoryService()
    return _memory_svc
