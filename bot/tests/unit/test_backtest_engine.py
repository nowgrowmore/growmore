"""Tests for growmore_bot.backtest.engine.BacktestEngine.

The critical behaviour under test: a signal generated on bar N must fill at
bar N+1's OPEN, never at bar N's own close/open -- this is what avoids
lookahead bias. A signal on the LAST bar has nothing to fill against and is
simply dropped (documented, not silently wrong).
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
        open=open_,
        high=high,
        low=low,
        close=close,
    )


class _SignalOnBarIndex(Strategy):
    """Test double: fires a fixed action on one specific bar index, HOLD otherwise."""

    def __init__(self, trigger_index: int, action: SignalAction):
        self.trigger_index = trigger_index
        self.action = action
        self._i = -1

    def on_bar(self, bar, position_state):
        self._i += 1
        if self._i == self.trigger_index:
            return Signal(action=self.action, size=1)
        return Signal(action=SignalAction.HOLD)


def test_buy_signal_on_bar_n_fills_at_bar_n_plus_1_open():
    bars = [
        _bar(1, open_=100, high=105, low=95, close=102),   # bar 0 -- signal fires here
        _bar(2, open_=110, high=112, low=108, close=111),  # bar 1 -- must fill HERE, at open=110
        _bar(3, open_=120, high=122, low=118, close=121),
    ]
    strategy = _SignalOnBarIndex(trigger_index=0, action=SignalAction.BUY)
    engine = BacktestEngine(strategy=strategy, initial_capital=100_000)

    result = engine.run(bars)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.side == "buy"
    assert trade.entry_price == pytest.approx(110)  # bar 1's OPEN, not bar 0's close (102)
    assert trade.entry_price != pytest.approx(bars[0].close)
    assert trade.entered_at == bars[1].timestamp


def test_signal_on_last_bar_has_no_next_bar_and_is_dropped():
    bars = [
        _bar(1, open_=100, high=105, low=95, close=102),
        _bar(2, open_=110, high=112, low=108, close=111),
    ]
    strategy = _SignalOnBarIndex(trigger_index=1, action=SignalAction.BUY)  # last bar
    engine = BacktestEngine(strategy=strategy, initial_capital=100_000)

    result = engine.run(bars)

    assert result.trades == []


def test_sell_closes_open_position_and_records_pnl():
    bars = [
        _bar(1, open_=100, high=105, low=95, close=102),   # BUY signal fires
        _bar(2, open_=110, high=112, low=108, close=111),  # fill entry @110; SELL signal fires
        _bar(3, open_=130, high=132, low=128, close=131),  # fill exit @130
    ]

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

    engine = BacktestEngine(strategy=BuyThenSell(), initial_capital=100_000)
    result = engine.run(bars)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_price == pytest.approx(110)
    assert trade.exit_price == pytest.approx(130)
    assert trade.pnl == pytest.approx(20)  # (130 - 110) * qty 1


def test_lot_size_scales_pnl_and_mark_to_market_but_not_prices():
    """Regression: BacktestEngine used to treat every instrument as "1 raw unit
    of the price series," making Sharpe/Max Drawdown incomparable across
    commodities at very different price levels and lot sizes (e.g. Gold Mini
    100g vs Copper 2500kg). `lot_size` must scale quantity (and therefore P&L
    and mark-to-market equity) without touching the recorded entry/exit prices
    themselves.
    """
    bars = [
        _bar(1, open_=100, high=105, low=95, close=102),   # BUY signal fires
        _bar(2, open_=110, high=112, low=108, close=111),  # fill entry @110; SELL signal fires
        _bar(3, open_=130, high=132, low=128, close=131),  # fill exit @130
    ]

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

    engine = BacktestEngine(strategy=BuyThenSell(), initial_capital=100_000, lot_size=2500)
    result = engine.run(bars)

    assert len(result.trades) == 1
    trade = result.trades[0]
    # Prices themselves are untouched by lot_size.
    assert trade.entry_price == pytest.approx(110)
    assert trade.exit_price == pytest.approx(130)
    # P&L scales by lot_size: (130 - 110) * (1 * 2500).
    assert trade.pnl == pytest.approx(20 * 2500)

    # Mark-to-market while the position is open (bar 2, before the exit fills)
    # must also reflect the full lot-scaled quantity, not 1 raw unit.
    equity_while_open = result.equity_curve[1].equity
    cash_after_entry = 100_000 - (110 * 2500)
    assert equity_while_open == pytest.approx(cash_after_entry + 111 * 2500)


def test_lot_size_defaults_to_one_for_backward_compatibility():
    bars = [
        _bar(1, open_=100, high=105, low=95, close=102),
        _bar(2, open_=110, high=112, low=108, close=111),
        _bar(3, open_=130, high=132, low=128, close=131),
    ]

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

    engine = BacktestEngine(strategy=BuyThenSell(), initial_capital=100_000)
    result = engine.run(bars)

    assert result.trades[0].pnl == pytest.approx(20)


def test_equity_curve_has_one_point_per_bar():
    bars = [
        _bar(1, open_=100, high=105, low=95, close=102),
        _bar(2, open_=110, high=112, low=108, close=111),
        _bar(3, open_=120, high=122, low=118, close=121),
    ]
    strategy = _SignalOnBarIndex(trigger_index=99, action=SignalAction.HOLD)
    engine = BacktestEngine(strategy=strategy, initial_capital=50_000)
    result = engine.run(bars)

    assert len(result.equity_curve) == len(bars)
    assert result.equity_curve[0].equity == pytest.approx(50_000)
