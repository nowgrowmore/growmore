"""Tests for BuyAndHoldStrategy.

Not a smoke test like always_flip -- this is a real, tradeable config and the
benchmark every other strategy is now measured against. Its whole behaviour is
"be long", and the only subtlety is that on MCX being long is not passive: the
contract expires, the scheduler force-closes before the delivery window, and
the position has to be re-established on the next month. So the one property
that matters is that it re-enters whenever it finds itself flat.
"""
from __future__ import annotations

from types import SimpleNamespace

from growmore_bot.strategies.base import SignalAction
from growmore_bot.strategies.buy_and_hold import BuyAndHoldStrategy


def _bar(close):
    return SimpleNamespace(open=close, high=close + 1, low=close - 1, close=close)


def _long(qty=1.0):
    return {"quantity": qty, "avg_entry_price": 100.0, "risk": {}}


def test_it_buys_on_the_very_first_bar():
    """No warm-up: there is nothing to compute. Any delay is just tracking
    error against the benchmark it exists to represent."""
    s = BuyAndHoldStrategy()
    assert s.on_bar(_bar(100), None).action == SignalAction.BUY


def test_it_holds_once_long_and_never_sells():
    s = BuyAndHoldStrategy()
    s.on_bar(_bar(100), None)
    for close in (110, 90, 200, 50, 10):
        assert s.on_bar(_bar(close), _long()).action == SignalAction.HOLD


def test_it_re_enters_after_an_expiry_roll_closes_the_position():
    """THE critical property. `contract_rollover` force-closes before the
    delivery window and repoints the instrument at the next contract month.
    A strategy that only ever bought once would then sit in cash forever and
    silently stop being buy-and-hold."""
    s = BuyAndHoldStrategy()
    assert s.on_bar(_bar(100), None).action == SignalAction.BUY
    assert s.on_bar(_bar(101), _long()).action == SignalAction.HOLD
    # Roll: the scheduler flattens us.
    assert s.on_bar(_bar(102), None).action == SignalAction.BUY
    assert s.on_bar(_bar(103), _long()).action == SignalAction.HOLD


def test_a_zero_quantity_position_row_counts_as_flat():
    """A closed position can leave a row with quantity 0 rather than no row
    at all; treating that as "in position" would strand the strategy."""
    s = BuyAndHoldStrategy()
    assert s.on_bar(_bar(100), {"quantity": 0.0}).action == SignalAction.BUY


def test_it_is_stateless_so_a_scheduler_restart_changes_nothing():
    """The scheduler rebuilds strategies every tick and warms them up with
    position_state=None. Anything remembered on the instance would be a lie
    by the time the live quote arrives."""
    s = BuyAndHoldStrategy()
    for _ in range(50):
        s.on_bar(_bar(100), None)
    assert s.get_state_snapshot() == {}
    assert not [k for k in vars(s) if not k.startswith("_")]


def test_it_does_not_ask_for_intraday_flattening():
    assert BuyAndHoldStrategy().requires_intraday_flatten is False


def test_debug_state_reports_a_permanently_bullish_stance():
    """So the dashboard and the companion-filter adapter can read it like any
    other trend strategy rather than needing a special case."""
    assert BuyAndHoldStrategy().debug_state()["stance"] == 1.0
