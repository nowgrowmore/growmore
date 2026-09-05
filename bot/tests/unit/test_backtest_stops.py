"""Intrabar stop handling in BacktestEngine.

This is the part of the risk layer that will silently invent money if it's
wrong, so each rule gets its own test:

  * A stop is a level known BEFORE the bar opens, so unlike a close-derived
    signal it is evaluated against that same bar's range, not the next one.
  * A bar that GAPS THROUGH the stop fills at the open, not at the stop.
    Overnight gaps on MCX metals are routine; filling at the stop level
    would book a price that was never available.
  * A stop fired on a bar beats any pending exit signal for that same bar.
  * Where one bar's range contains both the stop and a favourable level,
    OHLC alone can't order them -- the engine always assumes the adverse
    touch happened first.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from growmore_bot.backtest.engine import BacktestEngine
from growmore_bot.strategies.base import Signal, SignalAction, Strategy


def _bar(day, open_, high, low, close):
    return SimpleNamespace(
        timestamp=datetime(2024, 1, day, tzinfo=timezone.utc),
        open=open_, high=high, low=low, close=close,
    )


class _BuyOnceWithStop(Strategy):
    """Buys on the first bar with a fixed stop, then holds forever."""

    def __init__(self, stop_price):
        self._stop = stop_price
        self._i = -1

    def on_bar(self, bar, position_state):
        self._i += 1
        if self._i == 0:
            return Signal(action=SignalAction.BUY, size=1, stop_price=self._stop)
        return Signal(action=SignalAction.HOLD, stop_price=self._stop)


def test_a_stop_inside_the_bars_range_fills_at_the_stop_price():
    bars = [
        _bar(1, 100, 101, 99, 100),     # signal here
        _bar(2, 100, 102, 98, 101),     # entry fills at 100
        _bar(3, 100, 101, 88, 92),      # low 88 pierces the 90 stop
    ]
    result = BacktestEngine(strategy=_BuyOnceWithStop(90.0), initial_capital=100_000).run(bars)
    trade = result.trades[0]
    assert trade.exit_price == pytest.approx(90.0)
    assert trade.exit_reason == "stop"


def test_a_gap_open_through_the_stop_fills_at_the_open_not_the_stop():
    """The single most important rule here. A bar that opens below the stop
    never traded at the stop -- booking the stop level would be inventing a
    price. MCX metals gap overnight routinely."""
    bars = [
        _bar(1, 100, 101, 99, 100),
        _bar(2, 100, 102, 98, 101),     # entry at 100, stop at 90
        _bar(3, 85, 86, 80, 84),        # gaps open to 85, far below the stop
    ]
    result = BacktestEngine(strategy=_BuyOnceWithStop(90.0), initial_capital=100_000).run(bars)
    trade = result.trades[0]
    assert trade.exit_price == pytest.approx(85.0)     # the open, NOT 90
    assert trade.pnl == pytest.approx(85.0 - 100.0)


def test_an_untouched_stop_leaves_the_position_open():
    bars = [
        _bar(1, 100, 101, 99, 100),
        _bar(2, 100, 102, 98, 101),
        _bar(3, 101, 105, 95, 104),     # low 95 never reaches the 90 stop
    ]
    result = BacktestEngine(strategy=_BuyOnceWithStop(90.0), initial_capital=100_000).run(bars)
    assert result.trades[0].exit_price is None


class _BuyThenSellWithStop(Strategy):
    def __init__(self, stop_price):
        self._stop = stop_price
        self._i = -1

    def on_bar(self, bar, position_state):
        self._i += 1
        if self._i == 0:
            return Signal(action=SignalAction.BUY, size=1, stop_price=self._stop)
        if self._i == 1:
            return Signal(action=SignalAction.SELL, stop_price=self._stop)
        return Signal(action=SignalAction.HOLD, stop_price=self._stop)


def test_a_stop_beats_a_pending_exit_signal_on_the_same_bar():
    """Both want out; the stop is the adverse one, and OHLC can't prove the
    signal's fill came first. Assume the worse of the two."""
    bars = [
        _bar(1, 100, 101, 99, 100),
        _bar(2, 100, 102, 98, 101),     # entry at 100; SELL queued from here
        _bar(3, 95, 96, 85, 94),        # gaps to 95 AND pierces the 90 stop
    ]
    result = BacktestEngine(strategy=_BuyThenSellWithStop(90.0), initial_capital=100_000).run(bars)
    trade = result.trades[0]
    # The signal alone would have filled at the open, 95. The stop is worse.
    assert trade.exit_price == pytest.approx(90.0)
    assert trade.exit_reason == "stop"


def test_a_stop_reachable_on_the_entry_bar_itself_is_honoured():
    """You entered at the open, so a stop below it genuinely was reachable
    within that same bar -- it is not lookahead to check it."""
    bars = [
        _bar(1, 100, 101, 99, 100),
        _bar(2, 100, 102, 85, 88),      # entry at 100, and the low pierces 90
        _bar(3, 88, 89, 87, 88),
    ]
    result = BacktestEngine(strategy=_BuyOnceWithStop(90.0), initial_capital=100_000).run(bars)
    trade = result.trades[0]
    assert trade.exit_price == pytest.approx(90.0)
    assert trade.same_bar_stop is True


def test_no_stop_price_reproduces_the_original_stopless_behaviour():
    class _NoStop(Strategy):
        def __init__(self):
            self._i = -1

        def on_bar(self, bar, position_state):
            self._i += 1
            return Signal(action=SignalAction.BUY, size=1) if self._i == 0 else Signal(
                action=SignalAction.HOLD
            )

    bars = [
        _bar(1, 100, 101, 99, 100),
        _bar(2, 100, 102, 98, 101),
        _bar(3, 100, 101, 50, 60),      # a huge adverse move, no stop configured
    ]
    result = BacktestEngine(strategy=_NoStop(), initial_capital=100_000).run(bars)
    assert result.trades[0].exit_price is None


def test_the_equity_curve_agrees_with_the_trade_log_on_a_stopped_exit():
    """Regression: cash was credited at the PRE-slippage stop level while the
    trade recorded the slipped fill, so the equity curve was quietly more
    optimistic than the trades it was built from."""
    from growmore_bot.costs import CostModel

    model = CostModel(
        brokerage_per_order=0.0, brokerage_pct=0.0, exchange_txn_pct=0.0,
        ctt_sell_pct=0.0, stamp_buy_pct=0.0, sebi_pct=0.0, gst_pct=0.0,
        slippage_ticks=1.0, stop_slippage_ticks=1.0,
    )
    bars = [
        _bar(1, 100, 101, 99, 100),
        _bar(2, 100, 102, 98, 101),
        _bar(3, 100, 101, 88, 92),
        _bar(4, 92, 93, 91, 92),
    ]
    result = BacktestEngine(
        strategy=_BuyOnceWithStop(90.0), initial_capital=100_000,
        cost_model=model, tick_size=1.0,
    ).run(bars)
    trade = result.trades[0]
    # Entry slipped up 1 tick to 101; stop exit slipped down 2 ticks to 88.
    assert trade.entry_price == pytest.approx(101.0)
    assert trade.exit_price == pytest.approx(88.0)
    assert result.final_equity == pytest.approx(100_000 + trade.pnl)
