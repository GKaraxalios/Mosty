"""Tests for conversation repository (in-memory backend).

Run with: pytest tests/test_conversation_repository.py -v
No MySQL required when MEMORY_BACKEND=memory (default).
"""

import pytest

from app.models.schemas import ChatSession
from app.repositories.memory_repository import InMemoryConversationRepository


@pytest.fixture
def repo() -> InMemoryConversationRepository:
    return InMemoryConversationRepository()


def test_create_session(repo: InMemoryConversationRepository) -> None:
    session = repo.create_session(language="el")
    assert isinstance(session, ChatSession)
    assert len(session.session_id) == 16
    assert session.language == "el"
    assert session.conversation_history == []
    assert session.retrieved_skus == []


def test_get_session_not_found(repo: InMemoryConversationRepository) -> None:
    assert repo.get_session("nonexistent") is None


def test_get_or_create_creates_when_none(repo: InMemoryConversationRepository) -> None:
    session = repo.get_or_create(None, language="el")
    assert session.session_id
    assert repo.get_session(session.session_id) is not None


def test_get_or_create_returns_existing(repo: InMemoryConversationRepository) -> None:
    created = repo.create_session(language="el")
    found = repo.get_or_create(created.session_id, language="en")
    assert found.session_id == created.session_id
    assert found.language == created.language


def test_add_turn_and_get_context(repo: InMemoryConversationRepository) -> None:
    session = repo.create_session()
    repo.add_turn(session.session_id, "user", "Hello")
    repo.add_turn(session.session_id, "assistant", "Hi there")
    ctx = repo.get_conversation_context(session.session_id, max_turns=10)
    assert "User: Hello" in ctx
    assert "Assistant: Hi there" in ctx


def test_add_turn_no_op_when_session_missing(repo: InMemoryConversationRepository) -> None:
    repo.add_turn("nonexistent", "user", "Hello")
    assert repo.get_conversation_context("nonexistent") == ""


def test_delete_session(repo: InMemoryConversationRepository) -> None:
    session = repo.create_session()
    assert repo.delete_session(session.session_id) is True
    assert repo.get_session(session.session_id) is None
    assert repo.delete_session(session.session_id) is False


def test_update_session(repo: InMemoryConversationRepository) -> None:
    session = repo.create_session()
    repo.update_session(session.session_id, last_query="q", last_answer="a", language="el")
    updated = repo.get_session(session.session_id)
    assert updated is not None
    assert updated.last_query == "q"
    assert updated.last_answer == "a"
    assert updated.language == "el"


def test_add_retrieved_skus(repo: InMemoryConversationRepository) -> None:
    session = repo.create_session()
    repo.add_retrieved_skus(session.session_id, ["SKU1", "SKU2"])
    repo.add_retrieved_skus(session.session_id, ["SKU2", "SKU3"])
    updated = repo.get_session(session.session_id)
    assert updated is not None
    assert set(updated.retrieved_skus) == {"SKU1", "SKU2", "SKU3"}


def test_clear_session_history(repo: InMemoryConversationRepository) -> None:
    session = repo.create_session()
    repo.add_turn(session.session_id, "user", "Hi")
    assert repo.clear_session_history(session.session_id) is True
    assert repo.get_conversation_context(session.session_id) == ""
    assert repo.get_session(session.session_id) is not None
    assert repo.clear_session_history("nonexistent") is False


def test_list_sessions(repo: InMemoryConversationRepository) -> None:
    s1 = repo.create_session()
    s2 = repo.create_session()
    listed = repo.list_sessions(limit=10, offset=0)
    ids = {s.session_id for s in listed}
    assert s1.session_id in ids
    assert s2.session_id in ids
