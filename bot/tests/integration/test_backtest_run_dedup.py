"""Regression test: re-running the sweep for the same (strategy, instrument)
pairing must not pile up duplicate `BacktestRun` rows.

Found live 2026-09-05: `run_all.py`'s `main()` correctly looks up/creates the
`Strategy` row by `(name, version)` (see test_strategy_row_per_params.py),
but never deleted a PRIOR `BacktestRun` for that same pairing before
persisting a fresh one -- purely additive. Four re-runs across one day
(cost model, ATR fix, ensemble, shorting -- each a real code change) left 4
duplicate rows per strategy/instrument on the real Neon database, all
showing identical strategy/version/params on the dashboard except for their
real Sharpe/CAGR and `started_at`, since the Backtests/Rankings page has no
"latest run per pairing" dedup. Fixed with `delete_existing_runs`, called
right before persisting.

Skips (like the other integration tests here) if Postgres isn't reachable.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
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


def test_delete_existing_runs_removes_only_prior_runs_for_the_same_pairing(migrated_engine):
    from growmore_bot.backtest.run_all import delete_existing_runs
    from growmore_bot.persistence.models import (
        BacktestRun,
        BacktestTrade,
        Instrument,
    )
    from growmore_bot.persistence.models import (
        Strategy as StrategyRow,
    )

    with Session(migrated_engine) as session:
        strategy = StrategyRow(id=uuid.uuid4(), name="macd_trend", version="v1", params={})
        instrument_a = Instrument(
            id=uuid.uuid4(), symbol="GOLDM", name="Gold Mini", exchange_segment="MCX_COMM",
            security_id="1", lot_size=10,
        )
        instrument_b = Instrument(
            id=uuid.uuid4(), symbol="COPPER", name="Copper", exchange_segment="MCX_COMM",
            security_id="2", lot_size=2500,
        )
        session.add_all([strategy, instrument_a, instrument_b])
        session.flush()

        now = datetime.now(timezone.utc)

        def _make_run(instrument_id, sharpe):
            return BacktestRun(
                id=uuid.uuid4(), strategy_id=strategy.id, instrument_id=instrument_id,
                started_at=now, period_start=now, period_end=now, sharpe_ratio=sharpe,
            )

        old_run_a1 = _make_run(instrument_a.id, 1.0)
        old_run_a2 = _make_run(instrument_a.id, 1.2)  # a second stale re-run, same pairing
        run_b = _make_run(instrument_b.id, 0.8)  # a different instrument -- must survive
        session.add_all([old_run_a1, old_run_a2, run_b])
        session.flush()

        # A trade attached to one of the stale runs -- must cascade-delete,
        # not orphan or block the run's own deletion.
        trade = BacktestTrade(
            id=uuid.uuid4(), backtest_run_id=old_run_a1.id, entered_at=now, exited_at=now,
            side="buy", entry_price=100, exit_price=110, pnl=10,
        )
        session.add(trade)
        session.commit()

        delete_existing_runs(session, strategy.id, instrument_a.id)
        session.commit()

        remaining = session.query(BacktestRun).filter_by(strategy_id=strategy.id).all()
        assert [r.id for r in remaining] == [run_b.id]
        assert session.query(BacktestTrade).filter_by(backtest_run_id=old_run_a1.id).count() == 0
