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
