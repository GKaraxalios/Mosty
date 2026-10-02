"""Database engine and session factory (sync SQLAlchemy 2.0 + PyMySQL)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from urllib.parse import quote_plus

from app.core.config import get_settings
from app.db.models import Base

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _get_database_url() -> str:
    settings = get_settings()
    url = settings.DATABASE_URL
    if url and url.strip():
        url = url.strip()
        if url.startswith("mysql://"):
            url = url.replace("mysql://", "mysql+pymysql://", 1)
        return url

    user = quote_plus(settings.DB_USER or "")
    password = quote_plus(settings.DB_PASSWORD or "")

    return (
        f"mysql+pymysql://{user}:{password}"
        f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
        f"?charset={settings.DB_CHARSET}"
    )

def get_engine() -> Engine:
    """Create or return the shared SQLAlchemy engine (sync)."""
    global _engine
    if _engine is None:
        url = _get_database_url()
        _engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_recycle=300,
            echo=False,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return session factory bound to the engine."""
    global _session_factory
    if _session_factory is None:
        engine = get_engine()
        _session_factory = sessionmaker(
            bind=engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
    return _session_factory


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Context manager for a single DB session with commit/rollback."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_tables() -> None:
    """Create all tables (idempotent; safe to call on existing DB)."""
    Base.metadata.create_all(bind=get_engine())
