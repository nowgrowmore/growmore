"""Unit test for research.validation.deflate_sweep.persist_dsr -- the one
write this otherwise read-only report script does. No real DB connection:
`_engine()` is monkeypatched to return a fake SQLAlchemy-shaped engine.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from research.validation import deflate_sweep


class _FakeConn:
    def __init__(self):
        self.executed: list[tuple[str, dict]] = []

    def execute(self, stmt, params):
        self.executed.append((str(stmt), params))


@pytest.fixture
def fake_engine(monkeypatch):
    conn = _FakeConn()

    @contextmanager
    def fake_begin():
        yield conn

    engine = MagicMock()
    engine.begin = fake_begin
    monkeypatch.setattr(deflate_sweep, "_engine", lambda: engine)
    return conn


def test_persist_dsr_writes_one_update_per_run(fake_engine):
    n = deflate_sweep.persist_dsr({"run-1": 0.96, "run-2": 0.42})

    assert n == 2
    assert len(fake_engine.executed) == 2
    written = {p["id"]: p["dsr"] for _, p in fake_engine.executed}
    assert written == {"run-1": 0.96, "run-2": 0.42}


def test_persist_dsr_skips_nan_values_without_writing_them(fake_engine):
    n = deflate_sweep.persist_dsr({"run-1": 0.96, "run-2": float("nan")})

    assert n == 1
    assert len(fake_engine.executed) == 1
    assert fake_engine.executed[0][1]["id"] == "run-1"


def test_persist_dsr_returns_zero_and_touches_nothing_when_empty(fake_engine):
    n = deflate_sweep.persist_dsr({})

    assert n == 0
    assert fake_engine.executed == []


def test_persist_dsr_returns_zero_when_every_value_is_nan(fake_engine):
    n = deflate_sweep.persist_dsr({"run-1": float("nan")})

    assert n == 0
    assert fake_engine.executed == []
