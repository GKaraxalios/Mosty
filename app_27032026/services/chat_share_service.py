from __future__ import annotations

import hashlib
import secrets
import json
from datetime import datetime, timedelta, timezone

from app.models.schemas import SharedConversationMessage, SharedConversationPayload
from app.repositories.conversation_repository import get_conversation_repository


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ChatShareService:
    def __init__(self) -> None:
        self._repo = get_conversation_repository()

    def get_share_status(self, session_id: str, base_url: str) -> dict:
        share = self._repo.get_session_share(session_id)
        if not share:
            return {
                "session_id": session_id,
                "is_shared": False,
                "share_url": None,
                "created_at": None,
                "expires_at": None,
                "revoked_at": None,
            }
        return {
            "session_id": session_id,
            "is_shared": True,
            "share_url": None,
            "created_at": share.get("created_at"),
            "expires_at": share.get("expires_at"),
            "revoked_at": share.get("revoked_at"),
        }

    def create_share(self, session_id: str, base_url: str, expires_in_hours: int | None = None) -> dict:
        session = self._repo.get_session(session_id)
        if not session:
            raise ValueError("Session not found")
        if not session.conversation_history:
            raise RuntimeError("Empty conversation cannot be shared")

        raw_token = secrets.token_urlsafe(32)
        token_hash = _token_hash(raw_token)
        expires_at = None
        if expires_in_hours:
            expires_at = (datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        share = self._repo.upsert_session_share(session_id, token_hash=token_hash, expires_at=expires_at)
        return {
            "session_id": session_id,
            "is_shared": True,
            "share_url": self._build_share_url(base_url, raw_token, hashed=False),
            "created_at": share.get("created_at") or _iso_now(),
            "expires_at": share.get("expires_at"),
            "revoked_at": None,
        }

    def revoke_share(self, session_id: str) -> dict:
        revoked = self._repo.revoke_session_share(session_id)
        return {
            "session_id": session_id,
            "is_shared": False,
            "share_url": None,
            "created_at": None,
            "expires_at": None,
            "revoked_at": _iso_now() if revoked else None,
        }

    def get_shared_conversation(self, token: str) -> SharedConversationPayload | None:
        resolved = self._repo.get_shared_session_by_token_hash(_token_hash(token))
        if not resolved:
            return None
        session, _share = resolved
        safe_messages: list[SharedConversationMessage] = []
        for m in session.conversation_history:
            if m.role not in {"user", "assistant"}:
                continue
            content = m.content
            sources = []
            products = []
            intent = None
            if m.role == "assistant":
                parsed = self._parse_assistant_envelope(m.content)
                if parsed is not None:
                    content = parsed.get("content", content)
                    sources = parsed.get("sources", [])
                    products = parsed.get("products", [])
                    intent = parsed.get("intent")
            safe_messages.append(
                SharedConversationMessage(
                    role=m.role,
                    content=content,
                    timestamp=m.timestamp or "",
                    sources=sources,
                    products=products,
                    intent=intent,
                )
            )
        title = (session.last_query or "").strip()
        if not title:
            for msg in safe_messages:
                if msg.role == "user" and msg.content.strip():
                    title = msg.content.strip()
                    break
        title = (title or "Shared conversation")[:80]
        return SharedConversationPayload(
            title=title,
            language=session.language or "auto",
            created_at=session.created_at or None,
            updated_at=session.updated_at or None,
            messages=safe_messages,
        )

    def resolve_shared_session_id(self, token: str) -> str | None:
        resolved = self._repo.get_shared_session_by_token_hash(_token_hash(token))
        if not resolved:
            return None
        session, _share = resolved
        return session.session_id

    def _build_share_url(self, base_url: str, token: str, hashed: bool) -> str:
        if hashed:
            return ""
        return f"{base_url.rstrip('/')}/shared/chat/{token}"

    def _parse_assistant_envelope(self, content: str) -> dict | None:
        s = (content or "").strip()
        if not s.startswith("{"):
            return None
        try:
            data = json.loads(s)
        except Exception:
            return None
        if not isinstance(data, dict) or data.get("v") != 1:
            return None
        text = data.get("text")
        sources = data.get("sources")
        products = data.get("products")
        return {
            "content": text if isinstance(text, str) else "",
            "sources": sources if isinstance(sources, list) else [],
            "products": products if isinstance(products, list) else [],
            "intent": data.get("intent") if isinstance(data.get("intent"), str) else None,
        }

