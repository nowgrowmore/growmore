"""Tests for BacktestEngine.run_and_persist against an in-memory SQLite DB.

Confirms backtest_runs/backtest_trades/equity_curve_points rows are created
correctly and linked by FK, without needing a real Postgres.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from growmore_bot.backtest.engine import BacktestEngine
from growmore_bot.persistence.models import BacktestRun, BacktestTrade, Base, EquityCurvePoint
from growmore_bot.strategies.base import Signal, SignalAction, Strategy


def _bar(day, open_, high, low, close):
    return SimpleNamespace(
        timestamp=datetime(2024, 1, day, tzinfo=timezone.utc),
        open=open_,
        high=high,
        low=low,
        close=close,
    )


class BuyThenSell(Strategy):
    def __init__(self):
        self._i = -1

    def on_bar(self, bar, position_state):
        self._i += 1
        if self._i == 0:
            return Signal(action=SignalAction.BUY, size=1)
        if self._i == 1:
            return Signal(action=SignalAction.SELL, size=1)
        return Signal(action=SignalAction.HOLD)


def test_run_and_persist_writes_expected_rows():
    engine_db = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine_db)

    bars = [
        _bar(1, 100, 105, 95, 102),
        _bar(2, 110, 112, 108, 111),
        _bar(3, 130, 132, 128, 131),
    ]

    strategy_id = uuid.uuid4()
    instrument_id = uuid.uuid4()

    with Session(engine_db) as session:
        engine = BacktestEngine(strategy=BuyThenSell(), initial_capital=100_000)
        run_row = engine.run_and_persist(
            bars,
            session=session,
            strategy_id=strategy_id,
            instrument_id=instrument_id,
            started_at=datetime.now(timezone.utc),
        )
        session.commit()
        run_id = run_row.id

    with Session(engine_db) as session:
        persisted_run = session.get(BacktestRun, run_id)
        assert persisted_run is not None
        assert persisted_run.strategy_id == strategy_id
        assert persisted_run.instrument_id == instrument_id

        trades = session.scalars(
            select(BacktestTrade).where(BacktestTrade.backtest_run_id == run_id)
        ).all()
        assert len(trades) == 1
        assert float(trades[0].pnl) == 20

        points = session.scalars(
            select(EquityCurvePoint).where(EquityCurvePoint.backtest_run_id == run_id)
        ).all()
        assert len(points) == 3


def _in_memory_session():
    engine_db = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine_db)
    return Session(engine_db)


_COST_BARS = [
    _bar(1, 100, 105, 95, 102),
    _bar(2, 110, 112, 108, 111),
    _bar(3, 130, 132, 128, 131),
]


def test_persists_the_capital_basis_and_cost_breakdown():
    """A stored run is uninterpretable without knowing what capital it was
    measured against: at one flat figure across instruments, CAGR ranks
    contract size as much as edge. `cost_model` is stored for the same
    reason -- so a run is reproducible rather than taken on trust."""
    from growmore_bot.costs import DEFAULT_COST_MODEL

    with _in_memory_session() as session:
        engine = BacktestEngine(
            strategy=BuyThenSell(),
            initial_capital=1_529_500,
            lot_size=10,
            cost_model=DEFAULT_COST_MODEL,
            tick_size=1.0,
        )
        run = engine.run_and_persist(
            _COST_BARS,
            session=session,
            strategy_id=uuid.uuid4(),
            instrument_id=uuid.uuid4(),
            started_at=datetime.now(timezone.utc),
        )
        session.commit()

        assert float(run.initial_capital) == 1_529_500
        assert run.cost_model["brokerage_per_order"] == 20.0
        assert run.cost_model["ctt_sell_pct"] == 0.0001
        assert float(run.total_transaction_cost) > 0
        # Costs can only reduce the net figure, never improve it.
        assert float(run.cagr_pct) < float(run.gross_cagr_pct)


def test_a_costless_run_records_a_null_cost_model_rather_than_a_fake_one():
    with _in_memory_session() as session:
        engine = BacktestEngine(strategy=BuyThenSell(), initial_capital=500_000)
        run = engine.run_and_persist(
            _COST_BARS,
            session=session,
            strategy_id=uuid.uuid4(),
            instrument_id=uuid.uuid4(),
            started_at=datetime.now(timezone.utc),
        )
        session.commit()
        assert run.cost_model is None
        assert float(run.total_transaction_cost) == 0.0
        assert float(run.initial_capital) == 500_000
