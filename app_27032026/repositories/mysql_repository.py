"""MySQL implementation of ConversationRepository using SQLAlchemy."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.sql import func

from app.core.logging import get_logger
from app.db.base import session_scope
from app.db.models import ChatMessageModel, ChatSessionModel, ChatSessionShareModel
from app.models.schemas import (
    ChatSession,
    ConversationTurn,
    UserPreferences,
)
from app.repositories.conversation_repository import ConversationRepository

logger = get_logger(__name__)

MAX_HISTORY_TURNS = 40
CONTEXT_MAX_CHARS_PER_TURN = 600


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


def _session_to_pydantic(row: ChatSessionModel, messages: list[ChatMessageModel] | None = None) -> ChatSession:
    """Map ORM session (+ optional messages) to Pydantic ChatSession."""
    prefs = row.user_preferences or {}
    if not isinstance(prefs, dict):
        prefs = {}
    skus = row.retrieved_skus
    if not isinstance(skus, list):
        skus = []
    history = []
    if messages is not None:
        for m in messages:
            history.append(
                ConversationTurn(
                    role=m.role,
                    content=m.content,
                    timestamp=m.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if m.created_at else "",
                )
            )
    return ChatSession(
        session_id=row.session_id,
        created_at=row.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if row.created_at else "",
        updated_at=row.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ") if row.updated_at else "",
        language=row.language or "auto",
        user_preferences=UserPreferences.model_validate(prefs),
        conversation_history=history,
        retrieved_skus=[str(s) for s in skus],
        last_query=row.last_query,
        last_answer=row.last_answer,
    )


class MySQLConversationRepository(ConversationRepository):
    """Persist sessions and messages in MySQL."""

    _tables_ready = False

    def __init__(self) -> None:
        if not MySQLConversationRepository._tables_ready:
            try:
                from app.db.base import create_tables
                create_tables()
                MySQLConversationRepository._tables_ready = True
            except Exception:
                pass

    def create_session(self, language: str = "auto") -> ChatSession:
        import uuid
        session_id = uuid.uuid4().hex[:16]
        now = datetime.now(timezone.utc)
        with session_scope() as db:
            row = ChatSessionModel(
                session_id=session_id,
                created_at=now,
                updated_at=now,
                language=language,
                user_preferences={},
                retrieved_skus=[],
            )
            db.add(row)
            db.flush()
            logger.info("Session created: %s", session_id)
            return _session_to_pydantic(row, messages=[])

    def get_session(self, session_id: str) -> ChatSession | None:
        with session_scope() as db:
            row = db.execute(
                select(ChatSessionModel).where(
                    ChatSessionModel.session_id == session_id,
                    ChatSessionModel.deleted_at.is_(None),
                )
            ).scalars().one_or_none()
            if not row:
                return None
            messages = list(
                db.execute(
                    select(ChatMessageModel)
                    .where(ChatMessageModel.session_id == session_id)
                    .order_by(ChatMessageModel.sort_order)
                ).scalars().all()
            )
            return _session_to_pydantic(row, messages=messages)

    def get_or_create(self, session_id: str | None, language: str = "auto") -> ChatSession:
        if session_id:
            existing = self.get_session(session_id)
            if existing:
                return existing
        return self.create_session(language)

    def delete_session(self, session_id: str) -> bool:
        with session_scope() as db:
            result = db.execute(delete(ChatSessionModel).where(ChatSessionModel.session_id == session_id))
            db.flush()
            deleted = result.rowcount > 0
            if deleted:
                logger.info("Session deleted: %s", session_id)
            return deleted

    def add_turn(self, session_id: str, role: str, content: str) -> None:
        with session_scope() as db:
            row = db.execute(
                select(ChatSessionModel).where(
                    ChatSessionModel.session_id == session_id,
                    ChatSessionModel.deleted_at.is_(None),
                )
            ).scalars().one_or_none()
            if not row:
                return
            next_order = db.execute(
                select(func.coalesce(func.max(ChatMessageModel.sort_order), -1) + 1).where(
                    ChatMessageModel.session_id == session_id
                )
            ).scalar() or 0
            msg = ChatMessageModel(
                session_id=session_id,
                role=role,
                content=content[:65535] if content else "",  # TEXT safe length
                sort_order=next_order,
            )
            db.add(msg)
            db.execute(
                update(ChatSessionModel).where(ChatSessionModel.session_id == session_id).values(updated_at=datetime.now(timezone.utc))
            )
            # Trim old messages: keep last MAX_HISTORY_TURNS
            count = db.execute(select(func.count(ChatMessageModel.id)).where(ChatMessageModel.session_id == session_id)).scalar() or 0
            if count > MAX_HISTORY_TURNS:
                to_remove = count - MAX_HISTORY_TURNS
                old_ids = db.execute(
                    select(ChatMessageModel.id)
                    .where(ChatMessageModel.session_id == session_id)
                    .order_by(ChatMessageModel.sort_order)
                    .limit(to_remove)
                ).scalars().all()
                if old_ids:
                    db.execute(delete(ChatMessageModel).where(ChatMessageModel.id.in_(old_ids)))

    def get_conversation_context(self, session_id: str, max_turns: int = 10) -> str:
        with session_scope() as db:
            rows = (
                db.execute(
                    select(ChatMessageModel)
                    .where(ChatMessageModel.session_id == session_id)
                    .order_by(ChatMessageModel.sort_order.desc())
                    .limit(max_turns)
                )
                .scalars().all()
            )
            if not rows:
                return ""
            rows = list(reversed(rows))
            lines = []
            for m in rows:
                prefix = "User" if m.role == "user" else "Assistant"
                content = _context_text(m.content or "")[:CONTEXT_MAX_CHARS_PER_TURN]
                lines.append(f"{prefix}: {content}")
            return "\n".join(lines)

    def update_session(self, session_id: str, **kwargs: Any) -> None:
        allowed = {"last_query", "last_answer", "language", "updated_at"}
        payload = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not payload:
            return
        payload["updated_at"] = datetime.now(timezone.utc)
        with session_scope() as db:
            db.execute(update(ChatSessionModel).where(
                ChatSessionModel.session_id == session_id,
                ChatSessionModel.deleted_at.is_(None),
            ).values(**payload))

    def add_retrieved_skus(self, session_id: str, skus: list[str]) -> None:
        with session_scope() as db:
            row = db.execute(
                select(ChatSessionModel).where(
                    ChatSessionModel.session_id == session_id,
                    ChatSessionModel.deleted_at.is_(None),
                )
            ).scalars().one_or_none()
            if not row:
                return
            existing = set(row.retrieved_skus or [])
            updated = list(existing)
            for s in skus:
                if s not in existing:
                    updated.append(s)
                    existing.add(s)
            if len(updated) != len(row.retrieved_skus or []):
                row.retrieved_skus = updated
                row.updated_at = datetime.now(timezone.utc)

    def update_preferences(self, session_id: str, **kwargs: Any) -> None:
        with session_scope() as db:
            row = db.execute(
                select(ChatSessionModel).where(
                    ChatSessionModel.session_id == session_id,
                    ChatSessionModel.deleted_at.is_(None),
                )
            ).scalars().one_or_none()
            if not row:
                return
            prefs = dict(row.user_preferences or {})
            for k, v in kwargs.items():
                if v is not None and k in UserPreferences.model_fields:
                    prefs[k] = v
            row.user_preferences = prefs
            row.updated_at = datetime.now(timezone.utc)

    def list_sessions(self, limit: int = 50, offset: int = 0) -> list[ChatSession]:
        with session_scope() as db:
            rows = (
                db.execute(
                    select(ChatSessionModel)
                    .where(ChatSessionModel.deleted_at.is_(None))
                    .order_by(ChatSessionModel.updated_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
                .scalars().all()
            )
            return [_session_to_pydantic(r, messages=[]) for r in rows]

    def clear_session_history(self, session_id: str) -> bool:
        with session_scope() as db:
            row = db.execute(
                select(ChatSessionModel).where(
                    ChatSessionModel.session_id == session_id,
                    ChatSessionModel.deleted_at.is_(None),
                )
            ).scalars().one_or_none()
            if not row:
                return False
            db.execute(delete(ChatMessageModel).where(ChatMessageModel.session_id == session_id))
            row.updated_at = datetime.now(timezone.utc)
            return True

    def _share_to_dict(self, row: ChatSessionShareModel) -> dict[str, Any]:
        return {
            "session_id": row.session_id,
            "share_token_hash": row.share_token_hash,
            "is_active": bool(row.is_active),
            "visibility_mode": row.visibility_mode,
            "created_at": row.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if row.created_at else None,
            "updated_at": row.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ") if row.updated_at else None,
            "expires_at": row.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ") if row.expires_at else None,
            "revoked_at": row.revoked_at.strftime("%Y-%m-%dT%H:%M:%SZ") if row.revoked_at else None,
            "access_count": int(row.access_count or 0),
            "last_accessed_at": row.last_accessed_at.strftime("%Y-%m-%dT%H:%M:%SZ") if row.last_accessed_at else None,
        }

    def get_session_share(self, session_id: str) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        with session_scope() as db:
            row = db.execute(
                select(ChatSessionShareModel).where(
                    ChatSessionShareModel.session_id == session_id,
                    ChatSessionShareModel.is_active.is_(True),
                )
            ).scalars().one_or_none()
            if not row:
                return None
            if row.expires_at and row.expires_at <= now:
                row.is_active = False
                row.revoked_at = now
                row.updated_at = now
                return None
            return self._share_to_dict(row)

    def upsert_session_share(self, session_id: str, token_hash: str, expires_at: str | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        exp_dt = None
        if expires_at:
            try:
                exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except Exception:
                exp_dt = None
        with session_scope() as db:
            row = db.execute(
                select(ChatSessionShareModel).where(ChatSessionShareModel.session_id == session_id)
            ).scalars().one_or_none()
            if row is None:
                row = ChatSessionShareModel(
                    session_id=session_id,
                    share_token_hash=token_hash,
                    is_active=True,
                    visibility_mode="public_readonly",
                    created_at=now,
                    updated_at=now,
                    expires_at=exp_dt,
                    revoked_at=None,
                    access_count=0,
                )
                db.add(row)
            else:
                row.share_token_hash = token_hash
                row.is_active = True
                row.visibility_mode = "public_readonly"
                row.updated_at = now
                row.expires_at = exp_dt
                row.revoked_at = None
            db.flush()
            return self._share_to_dict(row)

    def revoke_session_share(self, session_id: str) -> bool:
        now = datetime.now(timezone.utc)
        with session_scope() as db:
            row = db.execute(
                select(ChatSessionShareModel).where(
                    ChatSessionShareModel.session_id == session_id,
                    ChatSessionShareModel.is_active.is_(True),
                )
            ).scalars().one_or_none()
            if not row:
                return False
            row.is_active = False
            row.revoked_at = now
            row.updated_at = now
            return True

    def get_shared_session_by_token_hash(self, token_hash: str) -> tuple[ChatSession, dict[str, Any]] | None:
        now = datetime.now(timezone.utc)
        with session_scope() as db:
            share = db.execute(
                select(ChatSessionShareModel).where(
                    ChatSessionShareModel.share_token_hash == token_hash,
                    ChatSessionShareModel.is_active.is_(True),
                )
            ).scalars().one_or_none()
            if not share:
                return None
            if share.expires_at and share.expires_at <= now:
                share.is_active = False
                share.revoked_at = now
                share.updated_at = now
                return None

            row = db.execute(
                select(ChatSessionModel).where(
                    ChatSessionModel.session_id == share.session_id,
                    ChatSessionModel.deleted_at.is_(None),
                )
            ).scalars().one_or_none()
            if not row:
                return None
            messages = list(
                db.execute(
                    select(ChatMessageModel)
                    .where(ChatMessageModel.session_id == share.session_id)
                    .order_by(ChatMessageModel.sort_order)
                ).scalars().all()
            )
            share.access_count = int(share.access_count or 0) + 1
            share.last_accessed_at = now
            share.updated_at = now
            return _session_to_pydantic(row, messages=messages), self._share_to_dict(share)

