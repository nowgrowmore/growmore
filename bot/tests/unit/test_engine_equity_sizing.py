"""`BacktestEngine(size_to_equity=True)` -- re-derive the position size from
CURRENT equity at each entry, instead of holding a fixed quantity forever.

Why this exists. The engine sizes as `qty = (signal.size or 1) * lot_size`,
which is exactly right for futures: one lot is one lot, and an MCX contract
does not appreciate 100x. Applied to fifteen years of equities it silently
becomes something else. A stock bought at Rs 6 with a Rs 5,00,000 budget is
82,372 shares; when the stock reaches Rs 1,000 those same 82,372 shares cost
Rs 8.2 crore to re-enter, against an account that is nowhere near that. The
backtest therefore runs at escalating leverage and eventually destroys
itself -- on the real F&O universe this produced transaction costs of
162-352% OF CAPITAL and "drawdowns" above 100%, which is impossible for a
long-only book at 1x leverage and was the tell.

Buy-and-hold is immune, because it enters once and never re-enters. So the
bug does not merely add noise -- it penalises exactly the arm being tested
and flatters the benchmark, in a comparison whose entire purpose is to
decide between them.

Default stays False so every published MCX number is unchanged.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from growmore_bot.backtest.engine import BacktestEngine
from growmore_bot.strategies.base import Signal, SignalAction, Strategy


class _FlipEveryNBars(Strategy):
    """BUY, hold `hold` bars, SELL, wait `wait` bars, repeat."""

    def __init__(self, hold=3, wait=2):
        self.hold, self.wait = hold, wait
        self.i = -1

    def on_bar(self, bar, position_state):
        self.i += 1
        cycle = self.i % (self.hold + self.wait)
        if cycle == 0:
            return Signal(action=SignalAction.BUY)
        if cycle == self.hold:
            return Signal(action=SignalAction.SELL)
        return Signal(action=SignalAction.HOLD)


def _ramp(n=120, start=10.0, growth=1.05):
    """A stock that compounds hard -- the case that breaks fixed sizing."""
    bars, price = [], start
    for _ in range(n):
        bars.append(SimpleNamespace(timestamp=None, open=price, high=price * 1.02,
                                    low=price * 0.98, close=price, volume=1000.0))
        price *= growth
    return bars


def _run(bars, **kw):
    engine = BacktestEngine(
        strategy=_FlipEveryNBars(), initial_capital=100_000.0, lot_size=10_000, **kw
    )
    return engine.run(bars)


def test_the_default_is_unchanged_fixed_quantity_sizing():
    import inspect
    assert inspect.signature(BacktestEngine.__init__).parameters["size_to_equity"].default is False


def test_fixed_sizing_lets_notional_outgrow_the_account_on_a_compounding_stock():
    # 10,000 shares at Rs 10 is the whole Rs 1,00,000 account. By the end of
    # the ramp the same 10,000 shares are worth many times it.
    bars = _ramp()
    result = _run(bars)
    entries = [t for t in result.trades if t.entry_price is not None]
    last_notional = entries[-1].entry_price * 10_000
    assert last_notional > 10 * 100_000.0


def test_sizing_to_equity_keeps_every_entry_near_the_account_value():
    bars = _ramp()
    result = _run(bars, size_to_equity=True)
    entries = [t for t in result.trades if t.entry_price is not None and t.quantity]
    assert len(entries) > 3
    # Every entry's notional stays within a small multiple of the equity that
    # bought it -- no runaway leverage.
    for trade in entries:
        notional = abs(trade.entry_price * trade.quantity)
        assert notional <= 1.5 * result.final_equity + 100_000.0


def test_quantity_grows_as_the_account_grows():
    result = _run(_ramp(), size_to_equity=True)
    entries = [t for t in result.trades if t.quantity]
    first, last = abs(entries[0].quantity), abs(entries[-1].quantity)
    # The stock is ~100x dearer by the end, so the same rupees buy far fewer
    # shares -- quantity must FALL, which is the whole point.
    assert last < first


def test_a_flat_series_sizes_identically_under_both_modes():
    flat = [SimpleNamespace(timestamp=None, open=100.0, high=101.0, low=99.0,
                            close=100.0, volume=1.0) for _ in range(60)]
    fixed = _run(flat)
    scaled = _run(flat, size_to_equity=True)
    # With no drift and no costs the account never moves, so re-deriving the
    # size from equity must reproduce the fixed size.
    assert fixed.final_equity == pytest.approx(scaled.final_equity, rel=1e-6)


def test_it_never_sizes_below_one_unit():
    # A collapsed account must still be able to take a position rather than
    # silently stop trading and freeze the equity curve.
    crash = []
    price = 1000.0
    for _ in range(80):
        crash.append(SimpleNamespace(timestamp=None, open=price, high=price * 1.01,
                                     low=price * 0.99, close=price, volume=1.0))
        price *= 0.93
    result = _run(crash, size_to_equity=True)
    assert all(abs(t.quantity) >= 1 for t in result.trades if t.quantity)
