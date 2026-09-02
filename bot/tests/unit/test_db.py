"""Tests for growmore_bot.persistence.db session management.

Uses an in-memory SQLite engine to avoid any real DB dependency in unit tests.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from growmore_bot.persistence.db import get_session, normalize_database_url, session_scope


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "postgresql://postgres:postgres@localhost:5432/growmore_test",
            "postgresql+psycopg://postgres:postgres@localhost:5432/growmore_test",
        ),
        (
            "postgres://user:pw@ep-example.neon.tech/growmore",
            "postgresql+psycopg://user:pw@ep-example.neon.tech/growmore",
        ),
        (
            "postgresql+psycopg://already:normalized@localhost/db",
            "postgresql+psycopg://already:normalized@localhost/db",
        ),
        ("sqlite:///:memory:", "sqlite:///:memory:"),
    ],
)
def test_normalize_database_url_forces_psycopg3_driver(raw, expected):
    # Regression test: SQLAlchemy defaults a bare "postgresql://" URL to the
    # psycopg2 dialect, but this project only depends on psycopg3
    # (`psycopg[binary]`) -- without normalization, create_engine() raises
    # ModuleNotFoundError: No module named 'psycopg2' for every real Postgres
    # connection (Neon included, since Neon still hands out "postgres://").
    assert normalize_database_url(raw) == expected


def test_get_session_returns_context_manager_yielding_session(monkeypatch):
    from growmore_bot.persistence import db as db_module

    engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(db_module, "_engine", engine)
    monkeypatch.setattr(db_module, "_SessionFactory", db_module.sessionmaker(bind=engine))

    with get_session() as session:
        result = session.execute(text("SELECT 1")).scalar()
        assert result == 1


def test_session_scope_commits_on_success(monkeypatch):
    from growmore_bot.persistence import db as db_module

    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)"))
        conn.commit()
    monkeypatch.setattr(db_module, "_engine", engine)
    monkeypatch.setattr(db_module, "_SessionFactory", db_module.sessionmaker(bind=engine))

    with session_scope() as session:
        session.execute(text("INSERT INTO t (val) VALUES ('hello')"))

    with get_session() as session:
        row = session.execute(text("SELECT val FROM t")).scalar()
        assert row == "hello"


def test_session_scope_rolls_back_on_error(monkeypatch):
    from growmore_bot.persistence import db as db_module

    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)"))
        conn.commit()
    monkeypatch.setattr(db_module, "_engine", engine)
    monkeypatch.setattr(db_module, "_SessionFactory", db_module.sessionmaker(bind=engine))

    with pytest.raises(ValueError):
        with session_scope() as session:
            session.execute(text("INSERT INTO t (val) VALUES ('should be rolled back')"))
            raise ValueError("boom")

    with get_session() as session:
        count = session.execute(text("SELECT COUNT(*) FROM t")).scalar()
        assert count == 0
