from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Text, delete, func, select
from sqlalchemy.dialects.mysql import JSON, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import session_scope, create_tables
from app.db.models import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WishlistItemModel(Base):
    __tablename__ = "wishlist_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(VARCHAR(64), index=True)
    sku: Mapped[str] = mapped_column(VARCHAR(128), index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)


class SavedLookModel(Base):
    __tablename__ = "saved_looks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(VARCHAR(64), index=True)
    title: Mapped[str] = mapped_column(VARCHAR(255), nullable=False)
    occasion: Mapped[str | None] = mapped_column(VARCHAR(128), nullable=True)
    skus: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)


class ProductFeedbackModel(Base):
    __tablename__ = "product_feedback"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(VARCHAR(64), index=True)
    sku: Mapped[str] = mapped_column(VARCHAR(128), index=True)
    action: Mapped[str] = mapped_column(VARCHAR(64), index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)


class ChatEventModel(Base):
    __tablename__ = "chat_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(VARCHAR(64), index=True)
    event_type: Mapped[str] = mapped_column(VARCHAR(64), index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)


VALID_FEEDBACK_ACTIONS = {
    "like", "dislike", "reject", "too_expensive",
    "wrong_style", "wrong_size", "not_for_occasion",
}


class CommerceFeaturesService:
    _tables_ready = False

    def __init__(self) -> None:
        if not CommerceFeaturesService._tables_ready:
            try:
                create_tables()
                CommerceFeaturesService._tables_ready = True
            except Exception:
                pass

    def add_to_wishlist(self, session_id: str, sku: str, note: str | None = None) -> dict[str, Any]:
        with session_scope() as db:
            existing = db.execute(
                select(WishlistItemModel).where(
                    WishlistItemModel.session_id == session_id,
                    WishlistItemModel.sku == sku,
                )
            ).scalar_one_or_none()
            if existing:
                if note is not None:
                    existing.note = note
                return {"added": False, "sku": sku, "already_exists": True}
            db.add(WishlistItemModel(session_id=session_id, sku=sku, note=note))
            return {"added": True, "sku": sku, "already_exists": False}

    def remove_from_wishlist(self, session_id: str, sku: str) -> bool:
        with session_scope() as db:
            result = db.execute(
                delete(WishlistItemModel).where(
                    WishlistItemModel.session_id == session_id,
                    WishlistItemModel.sku == sku,
                )
            )
            return (result.rowcount or 0) > 0

    def list_wishlist(self, session_id: str) -> list[dict[str, Any]]:
        with session_scope() as db:
            rows = db.execute(
                select(WishlistItemModel)
                .where(WishlistItemModel.session_id == session_id)
                .order_by(WishlistItemModel.created_at.desc())
            ).scalars().all()
            return [
                {
                    "sku": r.sku,
                    "note": r.note,
                    "created_at": r.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if r.created_at else None,
                }
                for r in rows
            ]

    def save_look(
        self,
        session_id: str,
        title: str,
        skus: list[str],
        occasion: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        with session_scope() as db:
            row = SavedLookModel(
                session_id=session_id,
                title=title,
                occasion=occasion,
                skus=skus,
                note=note,
            )
            db.add(row)
            db.flush()
            return {
                "id": row.id,
                "title": row.title,
                "occasion": row.occasion,
                "skus": row.skus,
                "note": row.note,
                "created_at": row.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if row.created_at else None,
            }

    def list_saved_looks(self, session_id: str) -> list[dict[str, Any]]:
        with session_scope() as db:
            rows = db.execute(
                select(SavedLookModel)
                .where(SavedLookModel.session_id == session_id)
                .order_by(SavedLookModel.created_at.desc())
            ).scalars().all()
            return [
                {
                    "id": r.id,
                    "title": r.title,
                    "occasion": r.occasion,
                    "skus": r.skus or [],
                    "note": r.note,
                    "created_at": r.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if r.created_at else None,
                }
                for r in rows
            ]

    def delete_look(self, look_id: int, session_id: str) -> bool:
        with session_scope() as db:
            result = db.execute(
                delete(SavedLookModel).where(
                    SavedLookModel.id == look_id,
                    SavedLookModel.session_id == session_id,
                )
            )
            return (result.rowcount or 0) > 0

    def add_feedback(self, session_id: str, sku: str, action: str, reason: str | None = None) -> dict[str, Any]:
        if action not in VALID_FEEDBACK_ACTIONS:
            raise ValueError(
                f"Invalid feedback action '{action}'. "
                f"Must be one of: {', '.join(sorted(VALID_FEEDBACK_ACTIONS))}"
            )
        with session_scope() as db:
            db.add(ProductFeedbackModel(session_id=session_id, sku=sku, action=action, reason=reason))
            return {"recorded": True, "sku": sku, "action": action, "reason": reason}

    def track_event(self, session_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
        with session_scope() as db:
            db.add(ChatEventModel(session_id=session_id, event_type=event_type, payload=payload or {}))

    def analytics_summary(self) -> dict[str, Any]:
        with session_scope() as db:
            top_events = db.execute(
                select(ChatEventModel.event_type, func.count(ChatEventModel.id))
                .group_by(ChatEventModel.event_type)
                .order_by(func.count(ChatEventModel.id).desc())
                .limit(20)
            ).all()

            top_feedback = db.execute(
                select(ProductFeedbackModel.action, func.count(ProductFeedbackModel.id))
                .group_by(ProductFeedbackModel.action)
                .order_by(func.count(ProductFeedbackModel.id).desc())
                .limit(20)
            ).all()

            top_wishlist = db.execute(
                select(WishlistItemModel.sku, func.count(WishlistItemModel.id))
                .group_by(WishlistItemModel.sku)
                .order_by(func.count(WishlistItemModel.id).desc())
                .limit(20)
            ).all()

            most_liked = db.execute(
                select(ProductFeedbackModel.sku, func.count(ProductFeedbackModel.id))
                .where(ProductFeedbackModel.action == "like")
                .group_by(ProductFeedbackModel.sku)
                .order_by(func.count(ProductFeedbackModel.id).desc())
                .limit(20)
            ).all()

            most_rejected = db.execute(
                select(ProductFeedbackModel.sku, func.count(ProductFeedbackModel.id))
                .where(ProductFeedbackModel.action.in_(["dislike", "reject"]))
                .group_by(ProductFeedbackModel.sku)
                .order_by(func.count(ProductFeedbackModel.id).desc())
                .limit(20)
            ).all()

            no_result_count_val = db.execute(
                select(func.count(ChatEventModel.id))
                .where(ChatEventModel.event_type == "no_results")
            ).scalar() or 0

            unique_sessions_val = db.execute(
                select(func.count(func.distinct(ChatEventModel.session_id)))
            ).scalar() or 0

            top_occasions = db.execute(
                select(SavedLookModel.occasion, func.count(SavedLookModel.id))
                .where(SavedLookModel.occasion.is_not(None))
                .group_by(SavedLookModel.occasion)
                .order_by(func.count(SavedLookModel.id).desc())
                .limit(10)
            ).all()

            return {
                "top_events": [{"event_type": e, "count": c} for e, c in top_events],
                "top_feedback_actions": [{"action": a, "count": c} for a, c in top_feedback],
                "top_wishlist_skus": [{"sku": s, "count": c} for s, c in top_wishlist],
                "most_liked_skus": [{"sku": s, "count": c} for s, c in most_liked],
                "most_rejected_skus": [{"sku": s, "count": c} for s, c in most_rejected],
                "no_result_count": no_result_count_val,
                "unique_sessions": unique_sessions_val,
                "top_saved_look_occasions": [{"occasion": o, "count": c} for o, c in top_occasions],
            }