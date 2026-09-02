"""Database engine / session management.

`get_session()` is the simple context manager used everywhere for read/write
access; `session_scope()` additionally commits on success and rolls back (and
re-raises) on any exception -- use it for a unit of work that writes.

The engine is created lazily from `Settings().database_url` on first use so
importing this module never requires DATABASE_URL to be set (tests can swap
`_engine`/`_SessionFactory` directly, as in tests/unit/test_db.py).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Optional[Engine] = None
_SessionFactory: Optional[sessionmaker] = None


def normalize_database_url(url: str) -> str:
    """Force the psycopg3 driver so `create_engine` doesn't default to psycopg2
    (not a project dependency -- we depend on `psycopg[binary]`, i.e. psycopg3).
    Accepts both `postgresql://` and the `postgres://` shorthand some providers
    (Neon included) still hand out.
    """
    if url.startswith("postgresql+psycopg://") or url.startswith("postgresql+psycopg2://"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


def _ensure_initialized() -> None:
    global _engine, _SessionFactory
    if _engine is not None and _SessionFactory is not None:
        return
    from growmore_bot.config import Settings

    settings = Settings()
    _engine = create_engine(normalize_database_url(settings.database_url), future=True)
    _SessionFactory = sessionmaker(bind=_engine, future=True, expire_on_commit=False)


@contextmanager
def get_session() -> Iterator[Session]:
    """Yield a plain SQLAlchemy session. Caller is responsible for commit/rollback."""
    _ensure_initialized()
    assert _SessionFactory is not None
    session = _SessionFactory()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yield a session; commit on success, rollback and re-raise on error."""
    _ensure_initialized()
    assert _SessionFactory is not None
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = ["get_session", "session_scope", "sessionmaker", "normalize_database_url"]
