"""A research-only wrapper that gates one instrument's entries on another's trend.

The hypothesis: Silver Mini is the noisier, higher-beta expression of the same
precious-metals macro as Gold Mini, so silver's losing trades should cluster
where gold was NOT trending -- whipsaw that a calmer companion would have
vetoed. Gold and silver are cointegrated with a time-varying vector and silver
adjusts faster to disequilibrium (Ciner; Schweikert), and comovement tightens
in exactly the turmoil episodes that produce silver's drawdowns.

This lives in `research/` on purpose. Supporting it in production means
changing `Strategy.on_bar` to take a companion bar, which touches base.py and
all three engines -- a real cost that should be paid only if the effect is
real. So the effect gets measured first, with a date-keyed lookup table
instead of an interface change.

No lookahead: the companion state for date D is computed from the companion's
bar that CLOSED on D, which is exactly what the inner strategy sees of its own
instrument on D, and both are traded at D+1's open by the engine.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from growmore_bot.strategies.base import Signal, SignalAction, Strategy
from growmore_bot.strategies.registry import build_strategy


def trend_states(bars, strategy_name: str, params: dict) -> dict[date, bool]:
    """Replay a strategy over `bars` and record its bullish/bearish STANCE per day.

    Stance, not signal: an event-based read ("did it emit BUY today?") is true
    on perhaps 40 days in five years and would veto essentially everything.
    What the filter wants is the standing opinion, which for a MACD is
    `macd > signal` and for the ensemble is its own `stance`.
    """
    strategy = build_strategy(strategy_name, params)
    out: dict[date, bool] = {}
    for bar in bars:
        strategy.on_bar(bar, None)
        stance = _stance(strategy.debug_state() or {})
        if stance is not None:
            out[bar.timestamp.date()] = stance

    # A companion that never forms an opinion vetoes EVERY entry and the
    # filtered run silently reports zero trades, which reads as "the filter
    # destroyed the strategy" rather than "the adapter does not understand
    # this strategy's debug_state". Fail loudly instead.
    if not out:
        raise ValueError(
            f"{strategy_name} never produced a readable stance over {len(bars)} bars -- "
            f"its debug_state() keys are not handled by _stance()"
        )
    return out


def _stance(state: dict) -> Optional[bool]:
    """Read a bullish/bearish standing opinion out of a strategy's debug_state.

    Each branch matches one strategy family's own key names; there is no
    common field for this on the Strategy contract, which is precisely why a
    production version of the filter would need an interface change.
    """
    if state.get("stance") is not None:
        return bool(state["stance"])
    # ensemble_trend: a majority of its member speeds
    if state.get("votes_cast") and state.get("votes_needed") is not None:
        if state["votes_cast"] < state["votes_needed"]:
            return None
        return state["bullish_votes"] >= state["votes_needed"]
    if state.get("macd") is not None and state.get("signal") is not None:
        return state["macd"] > state["signal"]
    if state.get("fast_sma") is not None and state.get("slow_sma") is not None:
        return state["fast_sma"] > state["slow_sma"]
    return None


class CompanionFilteredStrategy(Strategy):
    """Pass every inner signal through, EXCEPT a BUY on a day the companion
    is bearish (or has no opinion yet), which becomes a HOLD.

    Exits are never suppressed. A filter that can block an exit is not a
    filter, it is a second strategy, and it would leave positions open that
    the inner rule wanted closed.
    """

    def __init__(self, inner: Strategy, companion: dict[date, bool],
                 require_known: bool = True) -> None:
        self.inner = inner
        self._companion = companion
        self._require_known = require_known
        self.vetoed = 0
        self.allowed = 0

    def on_bar(self, bar: Any, position_state: Any) -> Signal:
        signal = self.inner.on_bar(bar, position_state)
        if signal.action != SignalAction.BUY:
            return signal
        agrees = self._companion.get(bar.timestamp.date())
        if agrees is None:
            agrees = not self._require_known
        if agrees:
            self.allowed += 1
            return signal
        self.vetoed += 1
        # Drop the entry but keep any risk state the wrapper attached, so a
        # risk-managed inner does not lose track of itself.
        return Signal(action=SignalAction.HOLD, risk_state=signal.risk_state)

    def debug_state(self) -> dict:
        return dict(self.inner.debug_state() or {})

    def get_state_snapshot(self) -> dict:
        return {"inner": self.inner.get_state_snapshot()}

    def load_state_snapshot(self, snapshot: dict) -> None:
        self.inner.load_state_snapshot((snapshot or {}).get("inner") or {})
