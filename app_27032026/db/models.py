"""SQLAlchemy ORM models for chat_sessions and chat_messages."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.mysql import JSON, VARCHAR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for ORM models."""

    type_annotation_map = {
        dict[str, Any]: JSON,
    }


class ChatSessionModel(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(VARCHAR(64), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(6), nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(6), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    language: Mapped[str] = mapped_column(VARCHAR(16), nullable=False, default="auto")
    user_preferences: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    retrieved_skus: Mapped[list | None] = mapped_column(JSON, nullable=True)
    last_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(6), nullable=True, default=None)

    messages: Mapped[list["ChatMessageModel"]] = relationship(
        "ChatMessageModel",
        back_populates="session",
        order_by="ChatMessageModel.sort_order",
        cascade="all, delete-orphan",
    )


class ChatMessageModel(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(VARCHAR(64), ForeignKey("chat_sessions.session_id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(6), nullable=False, default=datetime.utcnow)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    session: Mapped["ChatSessionModel"] = relationship("ChatSessionModel", back_populates="messages")

    __table_args__ = (Index("ix_chat_messages_session_order", "session_id", "sort_order"),)


class ChatSessionShareModel(Base):
    __tablename__ = "chat_session_shares"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        VARCHAR(64),
        ForeignKey("chat_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    share_token_hash: Mapped[str] = mapped_column(VARCHAR(128), unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True, index=True)
    visibility_mode: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, default="public_readonly")
    created_at: Mapped[datetime] = mapped_column(DateTime(6), nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(6), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(6), nullable=True, default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(6), nullable=True, default=None)
    access_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(6), nullable=True, default=None)

    __table_args__ = (
        Index("ix_chat_session_shares_session_active", "session_id", "is_active"),
    )


class AIEvaluationModel(Base):
    """Stores AI assistant evaluation / benchmark results for admin monitoring."""

    __tablename__ = "ai_evaluations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(6), nullable=False, default=datetime.utcnow)

    test_query: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_response: Mapped[str] = mapped_column(Text, nullable=False)
    detected_intent: Mapped[str | None] = mapped_column(VARCHAR(128), nullable=True)
    requested_category: Mapped[str | None] = mapped_column(VARCHAR(256), nullable=True)
    returned_categories: Mapped[list | None] = mapped_column(JSON, nullable=True)  # list of str
    recommended_products: Mapped[list | None] = mapped_column(JSON, nullable=True)  # list of dict
    recommended_skus: Mapped[list | None] = mapped_column(JSON, nullable=True)  # list of str

    stock_validation_passed: Mapped[bool] = mapped_column(nullable=False, default=True)
    duplicate_validation_passed: Mapped[bool] = mapped_column(nullable=False, default=True)
    category_validation_passed: Mapped[bool] = mapped_column(nullable=False, default=True)
    memory_isolation_passed: Mapped[bool] = mapped_column(nullable=False, default=True)
    formatting_validation_passed: Mapped[bool] = mapped_column(nullable=False, default=True)
    hallucination_validation_passed: Mapped[bool] = mapped_column(nullable=False, default=True)

    intent_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    category_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    stock_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    styling_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    memory_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    format_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    fail_reasons: Mapped[list | None] = mapped_column(JSON, nullable=True)  # list of str
    raw_judge_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    model_name: Mapped[str | None] = mapped_column(VARCHAR(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    evaluation_status: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, default="pass")  # pass | warning | fail
    dataset_name: Mapped[str | None] = mapped_column(VARCHAR(128), nullable=True)

    __table_args__ = (
        Index("ix_ai_evaluations_created_at", "created_at"),
        Index("ix_ai_evaluations_status", "evaluation_status"),
        Index("ix_ai_evaluations_total_score", "total_score"),
    )


class UserProfileModel(Base):
    """Persistent user profile / preferences that survive server restarts."""

    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(VARCHAR(64), unique=True, nullable=False, index=True)
    budget_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    preferred_colors: Mapped[list | None] = mapped_column(JSON, nullable=True)
    preferred_categories: Mapped[list | None] = mapped_column(JSON, nullable=True)
    preferred_styles: Mapped[list | None] = mapped_column(JSON, nullable=True)
    preferred_sizes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    preferred_brands: Mapped[list | None] = mapped_column(JSON, nullable=True)
    fit_preference: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    occasion: Mapped[str | None] = mapped_column(VARCHAR(128), nullable=True)
    language: Mapped[str] = mapped_column(VARCHAR(16), nullable=False, default="auto")
    liked_skus: Mapped[list | None] = mapped_column(JSON, nullable=True)
    rejected_skus: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(6), nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(6), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class AlertModel(Base):
    """User alert subscriptions (restock, price drop, new items in category)."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(VARCHAR(64), nullable=False, index=True)
    alert_type: Mapped[str] = mapped_column(VARCHAR(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, default="active")
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(6), nullable=False, default=datetime.utcnow)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(6), nullable=True)

    __table_args__ = (
        Index("ix_alerts_session_type", "session_id", "alert_type"),
    )


class MerchandisingRuleModel(Base):
    """Admin-managed merchandising rules: featured SKUs, boosted brands, campaigns."""

    __tablename__ = "merchandising_rules"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rule_type: Mapped[str] = mapped_column(VARCHAR(64), nullable=False, index=True)
    value: Mapped[str] = mapped_column(VARCHAR(256), nullable=False)
    boost_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.1)
    label: Mapped[str | None] = mapped_column(VARCHAR(256), nullable=True)
    active: Mapped[bool] = mapped_column(nullable=False, default=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(6), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(6), nullable=False, default=datetime.utcnow)
