"""End-to-end integration test: real Postgres + Alembic migrations + a tiny
backtest run, asserting rows land correctly in backtest_runs/backtest_trades.

Skips (does not fail) the whole module if DATABASE_URL isn't reachable --
per CLAUDE.md, integration tests need a local/dockerized Postgres that isn't
guaranteed to be running everywhere this suite executes. See bot/README.md
for how to start one (`docker run -e POSTGRES_PASSWORD=postgres -p 5432:5432
postgres:16`).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from growmore_bot.persistence.db import normalize_database_url

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "sample_ohlc.csv"


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    # Fall back to the checked-in safe default for local/CI use.
    env_test = Path(__file__).parents[2] / ".env.test"
    if env_test.exists():
        for line in env_test.read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    return "postgresql://postgres:postgres@localhost:5432/growmore_test"


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
    reason=(
        f"Postgres not reachable at {DATABASE_URL!r} -- start one locally to run "
        "integration tests (see bot/README.md), e.g.:\n"
        "  docker run -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16\n"
        "  createdb -h localhost -U postgres growmore_test"
    ),
)


def _load_bars() -> list[SimpleNamespace]:
    df = pd.read_csv(FIXTURE_CSV, parse_dates=["date"])
    return [
        SimpleNamespace(
            timestamp=row.date.to_pydatetime().replace(tzinfo=timezone.utc),
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
        )
        for row in df.itertuples()
    ]


class _BuyThenSellOnBar2(SimpleNamespace):
    """Deterministic test strategy: BUY on the first bar, SELL on the third."""


def _make_strategy():
    from growmore_bot.strategies.base import Signal, SignalAction, Strategy

    class _Strategy(Strategy):
        def __init__(self):
            self._i = -1

        def on_bar(self, bar, position_state):
            self._i += 1
            if self._i == 0:
                return Signal(action=SignalAction.BUY, size=1)
            if self._i == 2:
                return Signal(action=SignalAction.SELL, size=1)
            return Signal(action=SignalAction.HOLD)

    return _Strategy()


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


def test_backtest_end_to_end_persists_expected_rows(migrated_engine):
    from growmore_bot.backtest.engine import BacktestEngine
    from growmore_bot.persistence.models import BacktestRun, BacktestTrade, Instrument, Strategy

    with Session(migrated_engine) as session:
        instrument = Instrument(
            id=uuid.uuid4(),
            symbol="GOLDM",
            exchange_segment="MCX_COMM",
            security_id="TEST-SECURITY-ID",
            name="Gold Mini (test fixture)",
        )
        strategy_row = Strategy(
            id=uuid.uuid4(), name="integration_test_strategy", version="1.0", params={}
        )
        session.add_all([instrument, strategy_row])
        session.flush()

        bars = _load_bars()
        engine = BacktestEngine(strategy=_make_strategy(), initial_capital=100_000)
        run_row = engine.run_and_persist(
            bars,
            session=session,
            strategy_id=strategy_row.id,
            instrument_id=instrument.id,
            started_at=datetime.now(timezone.utc),
        )
        session.commit()
        run_id = run_row.id

    with Session(migrated_engine) as session:
        persisted_run = session.get(BacktestRun, run_id)
        assert persisted_run is not None
        assert persisted_run.period_start is not None
        assert persisted_run.period_end is not None

        trades = session.scalars(
            select(BacktestTrade).where(BacktestTrade.backtest_run_id == run_id)
        ).all()
        assert len(trades) == 1
        assert float(trades[0].entry_price) == pytest.approx(110)  # bar 1's open
        assert float(trades[0].exit_price) == pytest.approx(125)  # bar 3's open
        assert float(trades[0].pnl) == pytest.approx(15)
