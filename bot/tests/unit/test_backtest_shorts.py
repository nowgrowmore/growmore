"""Short positions in BacktestEngine.

Commodity futures are symmetric -- shorting is ordinary, and Dhan's order
client already accepts SELL -- but every engine here has been long-only, so
the down half of every trend was simply not captured.

`allow_shorts` defaults to False so every existing result stays bit-for-bit
reproducible; these tests cover the flag turned on. The sign convention is
signed quantity (negative = short), chosen because it makes every
`(exit - entry) * qty` expression correct automatically rather than needing
a +/-1 threaded through each P&L site.
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


class _Scripted(Strategy):
    def __init__(self, actions):
        self._actions = list(actions)

    def on_bar(self, bar, position_state):
        action = self._actions.pop(0) if self._actions else SignalAction.HOLD
        return Signal(action=action, size=1)


FALLING = [
    _bar(1, 100, 101, 99, 100),
    _bar(2, 100, 101, 99, 100),
    _bar(3, 90, 91, 89, 90),
    _bar(4, 80, 81, 79, 80),
]


def test_long_only_is_still_the_default_and_ignores_a_sell_while_flat():
    """Every result already stored was produced this way; the default must
    not move."""
    engine = BacktestEngine(
        strategy=_Scripted([SignalAction.SELL]), initial_capital=100_000, lot_size=1
    )
    assert engine.run(FALLING).trades == []


def test_a_sell_while_flat_opens_a_short_when_shorts_are_allowed():
    engine = BacktestEngine(
        strategy=_Scripted([SignalAction.SELL]), initial_capital=100_000,
        lot_size=1, allow_shorts=True,
    )
    result = engine.run(FALLING)
    assert len(result.trades) == 1
    assert result.trades[0].side == "sell"
    assert result.trades[0].entry_price == pytest.approx(100.0)


def test_a_short_profits_when_price_falls():
    engine = BacktestEngine(
        strategy=_Scripted([SignalAction.SELL, SignalAction.HOLD, SignalAction.BUY]),
        initial_capital=100_000, lot_size=1, allow_shorts=True,
    )
    trade = engine.run(FALLING).trades[0]
    # Short at 100 (bar 2 open), cover at 80 (bar 4 open) -> +20 a unit.
    assert trade.exit_price == pytest.approx(80.0)
    assert trade.pnl == pytest.approx(20.0)


def test_a_short_loses_when_price_rises():
    rising = [
        _bar(1, 100, 101, 99, 100),
        _bar(2, 100, 101, 99, 100),
        _bar(3, 110, 111, 109, 110),
        _bar(4, 120, 121, 119, 120),
    ]
    engine = BacktestEngine(
        strategy=_Scripted([SignalAction.SELL, SignalAction.HOLD, SignalAction.BUY]),
        initial_capital=100_000, lot_size=1, allow_shorts=True,
    )
    assert engine.run(rising).trades[0].pnl == pytest.approx(-20.0)


def test_a_reversal_closes_the_long_and_opens_a_short_in_one_step():
    """The subtle transition. Both legs fill at the same open, and BOTH pay
    costs -- treating a reversal as a single trade would understate the
    cost of turning a book around."""
    bars = [
        _bar(1, 100, 101, 99, 100),
        _bar(2, 100, 101, 99, 100),   # long opens here
        _bar(3, 110, 111, 109, 110),  # reversal fills here
        _bar(4, 90, 91, 89, 90),
    ]
    engine = BacktestEngine(
        strategy=_Scripted([SignalAction.BUY, SignalAction.SELL]),
        initial_capital=100_000, lot_size=1, allow_shorts=True,
    )
    result = engine.run(bars)
    assert len(result.trades) == 2
    assert result.trades[0].side == "buy"
    assert result.trades[0].exit_price == pytest.approx(110.0)
    assert result.trades[1].side == "sell"
    assert result.trades[1].entry_price == pytest.approx(110.0)


def test_the_equity_curve_marks_a_short_to_market_with_the_right_sign():
    engine = BacktestEngine(
        strategy=_Scripted([SignalAction.SELL]), initial_capital=100_000,
        lot_size=1, allow_shorts=True,
    )
    curve = engine.run(FALLING).equity_curve
    # Short at 100 on bar 2's open; price falls to 80, so equity must RISE.
    assert curve[-1].equity > curve[1].equity


def test_a_short_stop_sits_above_the_entry_and_fires_on_a_rally():
    class _ShortWithStop(Strategy):
        def __init__(self):
            self._i = -1

        def on_bar(self, bar, position_state):
            self._i += 1
            action = SignalAction.SELL if self._i == 0 else SignalAction.HOLD
            return Signal(action=action, size=1, stop_price=110.0)

    bars = [
        _bar(1, 100, 101, 99, 100),
        _bar(2, 100, 101, 99, 100),   # short opens at 100
        _bar(3, 102, 115, 101, 114),  # high 115 pierces the 110 stop
    ]
    engine = BacktestEngine(
        strategy=_ShortWithStop(), initial_capital=100_000, lot_size=1, allow_shorts=True
    )
    trade = engine.run(bars).trades[0]
    assert trade.exit_price == pytest.approx(110.0)
    assert trade.exit_reason == "stop"
    assert trade.pnl == pytest.approx(-10.0)


def test_a_short_stop_gapped_through_fills_at_the_open_not_the_stop():
    bars = [
        _bar(1, 100, 101, 99, 100),
        _bar(2, 100, 101, 99, 100),
        _bar(3, 125, 130, 124, 128),  # gaps ABOVE the 110 stop
    ]

    class _ShortWithStop(Strategy):
        def __init__(self):
            self._i = -1

        def on_bar(self, bar, position_state):
            self._i += 1
            action = SignalAction.SELL if self._i == 0 else SignalAction.HOLD
            return Signal(action=action, size=1, stop_price=110.0)

    engine = BacktestEngine(
        strategy=_ShortWithStop(), initial_capital=100_000, lot_size=1, allow_shorts=True
    )
    trade = engine.run(bars).trades[0]
    assert trade.exit_price == pytest.approx(125.0)   # the open, worse than the stop
