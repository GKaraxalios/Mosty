"""Persistent user profile service.

Stores user preferences in MySQL so they survive server restarts.
Merges with session-level preferences managed by MemoryService.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.base import session_scope
from app.db.models import UserProfileModel

logger = get_logger(__name__)

_PROFILE_FIELDS = {
    "budget_min", "budget_max", "preferred_colors", "preferred_categories",
    "preferred_styles", "preferred_sizes", "preferred_brands",
    "fit_preference", "occasion", "language", "liked_skus", "rejected_skus",
}


class UserProfileService:

    def get_profile(self, session_id: str) -> dict[str, Any] | None:
        with session_scope() as db:
            row = db.execute(
                select(UserProfileModel).where(UserProfileModel.session_id == session_id)
            ).scalar_one_or_none()
            if not row:
                return None
            return _row_to_dict(row)

    def upsert_profile(self, session_id: str, data: dict[str, Any]) -> dict[str, Any]:
        clean = {k: v for k, v in data.items() if k in _PROFILE_FIELDS and v is not None}
        with session_scope() as db:
            row = db.execute(
                select(UserProfileModel).where(UserProfileModel.session_id == session_id)
            ).scalar_one_or_none()
            if row:
                for key, val in clean.items():
                    setattr(row, key, val)
                row.updated_at = datetime.now(timezone.utc)
            else:
                row = UserProfileModel(session_id=session_id, **clean)
                db.add(row)
            db.flush()
            return _row_to_dict(row)

    def merge_with_session(self, session_id: str) -> dict[str, Any]:
        """Load persistent profile and push it into the session preferences."""
        profile = self.get_profile(session_id)
        if not profile:
            return {}

        from app.services.memory_service import get_memory_service
        memory = get_memory_service()
        session = memory.get_session(session_id)
        if not session:
            return profile

        update: dict[str, Any] = {}
        for field in _PROFILE_FIELDS:
            val = profile.get(field)
            if val is None or val == [] or val == "":
                continue
            current = getattr(session.user_preferences, field, None)
            if current is None or current == [] or current == "" or current == "auto":
                update[field] = val
            elif isinstance(val, list) and isinstance(current, list):
                merged = list(dict.fromkeys(current + val))
                update[field] = merged

        if update:
            memory.update_preferences(session_id, **update)
        return profile

    def sync_from_session(self, session_id: str) -> dict[str, Any]:
        """Pull current session preferences into the persistent profile."""
        from app.services.memory_service import get_memory_service
        memory = get_memory_service()
        session = memory.get_session(session_id)
        if not session:
            return {}
        prefs = session.user_preferences.model_dump(exclude_none=True)
        if not prefs:
            return {}
        return self.upsert_profile(session_id, prefs)


def _row_to_dict(row: UserProfileModel) -> dict[str, Any]:
    return {
        "session_id": row.session_id,
        "budget_min": row.budget_min,
        "budget_max": row.budget_max,
        "preferred_colors": row.preferred_colors or [],
        "preferred_categories": row.preferred_categories or [],
        "preferred_styles": row.preferred_styles or [],
        "preferred_sizes": row.preferred_sizes or [],
        "preferred_brands": row.preferred_brands or [],
        "fit_preference": row.fit_preference,
        "occasion": row.occasion,
        "language": row.language,
        "liked_skus": row.liked_skus or [],
        "rejected_skus": row.rejected_skus or [],
        "created_at": row.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if row.created_at else None,
        "updated_at": row.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ") if row.updated_at else None,
    }
