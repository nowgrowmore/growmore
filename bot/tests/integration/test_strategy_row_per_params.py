"""Regression test: distinct parameter variants of the same strategy family
must persist as distinct `strategies` rows, not silently collapse into one.

Found while planning the strategy/parameter sweep (docs/pending-actions.md):
`backtest/run_all.py` used to look up a `Strategy` row by `name` alone and
reuse whatever it found -- running it again with different hardcoded params
(e.g. a different SMA fast/slow pair) would silently persist new backtest
results under a stale row's old `params`, and the dashboard's
`{name} v{version}` display would show two runs as identical. Fixed by
looking up/creating by `(name, version)`, with `version` encoding the
parameter variant as a short readable label (e.g. "fast5-slow20"). Skips
(like the other integration tests here) if Postgres isn't reachable.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from growmore_bot.persistence.db import normalize_database_url


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env_test = Path(__file__).parents[2] / ".env.test"
    if env_test.exists():
        for line in env_test.read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    return "postgresql+psycopg://postgres:postgres@localhost:5432/growmore_test"


DATABASE_URL = _database_url()


def _postgres_reachable(url: str) -> bool:
    try:
        engine = create_engine(normalize_database_url(url), connect_args={"connect_timeout": 3})
        with engine.connect():
            return True
    except Exception:
        return False
    finally:
        try:
            engine.dispose()
        except Exception:
            pass


pytestmark = pytest.mark.skipif(
    not _postgres_reachable(DATABASE_URL),
    reason=f"Postgres not reachable at {DATABASE_URL!r} -- see bot/README.md",
)


@pytest.fixture(scope="module")
def migrated_engine():
    from alembic import command
    from alembic.config import Config

    bot_root = Path(__file__).parents[2]
    alembic_cfg = Config(str(bot_root / "alembic.ini"))
    alembic_cfg.set_main_option(
        "script_location", str(bot_root / "growmore_bot" / "persistence" / "migrations")
    )
    os.environ["DATABASE_URL"] = DATABASE_URL

    command.upgrade(alembic_cfg, "head")
    engine = create_engine(normalize_database_url(DATABASE_URL), future=True)
    try:
        yield engine
    finally:
        command.downgrade(alembic_cfg, "base")
        engine.dispose()


def _lookup_or_create(session, name, version, params):
    """Mirrors run_all.py's lookup-or-create logic exactly."""
    from growmore_bot.persistence.models import Strategy as StrategyRow

    row = session.query(StrategyRow).filter_by(name=name, version=version).one_or_none()
    if row is None:
        row = StrategyRow(id=uuid.uuid4(), name=name, version=version, params=params)
        session.add(row)
        session.flush()
    return row


def test_two_parameter_variants_of_the_same_strategy_name_get_distinct_rows(migrated_engine):
    from growmore_bot.persistence.models import Strategy as StrategyRow

    with Session(migrated_engine) as session:
        row_a = _lookup_or_create(
            session, "sma_crossover", "fast5-slow20", {"fast_period": 5, "slow_period": 20}
        )
        row_b = _lookup_or_create(
            session, "sma_crossover", "fast10-slow30", {"fast_period": 10, "slow_period": 30}
        )
        session.commit()

        assert row_a.id != row_b.id

        all_rows = session.query(StrategyRow).filter_by(name="sma_crossover").all()
        assert len(all_rows) == 2
        by_version = {r.version: r.params for r in all_rows}
        assert by_version["fast5-slow20"] == {"fast_period": 5, "slow_period": 20}
        assert by_version["fast10-slow30"] == {"fast_period": 10, "slow_period": 30}


def test_same_name_and_version_reuses_the_existing_row(migrated_engine):
    from growmore_bot.persistence.models import Strategy as StrategyRow

    with Session(migrated_engine) as session:
        first = _lookup_or_create(
            session, "donchian_breakout", "period10", {"period": 10}
        )
        session.commit()
        first_id = first.id

    with Session(migrated_engine) as session:
        second = _lookup_or_create(
            session, "donchian_breakout", "period10", {"period": 10}
        )
        session.commit()

        assert second.id == first_id
        count = (
            session.query(StrategyRow)
            .filter_by(name="donchian_breakout", version="period10")
            .count()
        )
        assert count == 1
