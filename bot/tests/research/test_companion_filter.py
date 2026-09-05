"""Tests for research.crosstrend.companion.

Two properties matter: a BUY is vetoed when the companion disagrees, and an
EXIT is never vetoed (a filter that can trap you in a position is a bug, not
a filter).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from growmore_bot.strategies.base import Signal, SignalAction, Strategy
from research.crosstrend.companion import CompanionFilteredStrategy, trend_states
from research.dailydata.cache import CachedBar


class _Scripted(Strategy):
    def __init__(self, actions):
        self._actions = list(actions)

    def on_bar(self, bar, position_state):
        return Signal(action=self._actions.pop(0) if self._actions else SignalAction.HOLD)

    def debug_state(self):
        return {}


def _bar(day):
    return CachedBar(timestamp=datetime(2024, 1, day), open=10, high=11, low=9, close=10)


def _close_bar(i, close):
    return CachedBar(
        timestamp=datetime(2021, 1, 1) + timedelta(days=i),
        open=close, high=close * 1.01, low=close * 0.99, close=close,
    )


def test_a_buy_is_vetoed_when_the_companion_disagrees():
    s = CompanionFilteredStrategy(_Scripted([SignalAction.BUY]), {date(2024, 1, 1): False})
    assert s.on_bar(_bar(1), None).action == SignalAction.HOLD
    assert s.vetoed == 1 and s.allowed == 0


def test_a_buy_passes_when_the_companion_agrees():
    s = CompanionFilteredStrategy(_Scripted([SignalAction.BUY]), {date(2024, 1, 1): True})
    assert s.on_bar(_bar(1), None).action == SignalAction.BUY
    assert s.allowed == 1 and s.vetoed == 0


def test_a_sell_is_never_vetoed_however_bearish_the_companion():
    """Blocking an exit would trap the position -- the one thing a filter
    must never do."""
    s = CompanionFilteredStrategy(_Scripted([SignalAction.SELL]), {date(2024, 1, 1): False})
    assert s.on_bar(_bar(1), None).action == SignalAction.SELL
    assert s.vetoed == 0


def test_an_unknown_companion_day_vetoes_by_default():
    s = CompanionFilteredStrategy(_Scripted([SignalAction.BUY]), {})
    assert s.on_bar(_bar(1), None).action == SignalAction.HOLD

    lenient = CompanionFilteredStrategy(_Scripted([SignalAction.BUY]), {}, require_known=False)
    assert lenient.on_bar(_bar(1), None).action == SignalAction.BUY


def test_trend_states_reads_a_standing_stance_not_a_crossover_event():
    """A MACD emits a handful of BUY events in five years; its STANCE has an
    opinion nearly every day. Filtering on events would veto everything."""
    # Compounding, not linear: a perfectly linear ramp drives MACD and its
    # signal line to converge to the SAME value, so `macd > signal` comes out
    # False on a floating-point hair and the fixture tests nothing.
    bars = [
        _close_bar(i, 100 * 1.01 ** i) for i in range(120)
    ]
    states = trend_states(bars, "macd_trend",
                          {"fast_period": 5, "slow_period": 13, "signal_period": 5})
    assert len(states) > 80, "a stance should exist on most days, not a few"
    assert all(v is True for v in states.values()), "a pure uptrend is bullish throughout"


def test_trend_states_flips_bearish_when_the_trend_reverses():
    """Asserted at the reversal rather than at the tail.

    A MACD stance on a COMPOUNDING decline turns bullish again once the fall
    decelerates in absolute terms -- 0.98^60 leaves the fast/slow gap tiny and
    rising against its own lagged signal. That is correct behaviour, not a
    bug, so the honest assertion is that the stance flips shortly after the
    turn, not that it stays bearish forever.
    """
    up = [100 * 1.01 ** i for i in range(60)]
    down = [up[-1] * 0.98 ** i for i in range(1, 61)]
    bars = [_close_bar(i, c) for i, c in enumerate(up + down)]

    states = list(trend_states(bars, "macd_trend",
                               {"fast_period": 5, "slow_period": 13,
                                "signal_period": 5}).values())
    # Warm-up eats the first ~16 bars, so the turn lands around index 44.
    assert all(states[35:43]), "should still be bullish before the turn"
    assert not any(states[45:60]), "should be bearish through the fall"


def test_the_ensemble_produces_a_readable_stance():
    """Regression: ensemble_trend's debug_state exposes vote COUNTS, not a
    `stance` or a `macd`/`signal` pair. The first version of _stance() knew
    neither, so it returned an opinion on zero days -- and because the filter
    vetoes unknown days, every filtered run silently reported ZERO trades,
    which looks exactly like a real (catastrophic) result."""
    bars = [_close_bar(i, 100 * 1.01 ** i) for i in range(200)]
    states = trend_states(bars, "ensemble_trend", {"min_agreement": 3})
    assert len(states) > 100
    assert states[max(states)] is True


def test_a_companion_that_never_forms_an_opinion_raises_instead_of_vetoing_everything():
    import pytest

    bars = [_close_bar(i, 100 * 1.01 ** i) for i in range(5)]
    with pytest.raises(ValueError, match="readable stance"):
        trend_states(bars, "ensemble_trend", {"min_agreement": 3})
