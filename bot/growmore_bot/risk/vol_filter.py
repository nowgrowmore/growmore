"""Refuse new entries when the instrument's own volatility is extreme.

Continuous volatility TARGETING -- the thing that actually drives the
Moskowitz/Ooi/Pedersen result -- is not expressible at this account size. At
Rs 5 lakh against Gold Mini's ~15% vol the sizing formula asks for ~0.3 lots,
which rounds to 0 or 1 and stays there; the alpha comes from continuously
scaling a position this account cannot scale.

Binary ADMISSION is expressible: one lot, or none. So instead of asking "how
big?" this asks "at all?", and refuses to open while realised volatility sits
in the top slice of its own recent history. The target is Silver Mini, whose
problem has never been return (it has the best CAGR in the book) but a
drawdown that ran to 28% before the Phase 0 stop fix and 20% after. Silver's
drawdowns are volatility events.

Two deliberate choices:

  * The threshold is a PERCENTILE OF THE INSTRUMENT'S OWN trailing history,
    not an absolute vol number. An absolute threshold tuned on gold would
    reject every silver bar and no copper bar -- the contracts differ by more
    than 3x in typical volatility.
  * Only ENTRIES are filtered. An exit is never blocked. A filter that can
    trap you in a position during a volatility spike is the exact opposite of
    a risk control.

`RealizedVolCalculator` already existed in indicators.py, fully tested, and
nothing outside its own test imported it.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Optional

from growmore_bot.indicators import RealizedVolCalculator
from growmore_bot.strategies.base import Signal, SignalAction, Strategy


class VolFilteredStrategy(Strategy):
    """Wrap any strategy; suppress its entries in the top `percentile_cap`
    slice of trailing realised volatility.

    Composes with RiskManagedStrategy in either order, but wrapping the
    risk-managed strategy (rather than being wrapped by it) is what you want:
    that way the stop logic still sees every bar and keeps managing an open
    position, and only the decision to OPEN is gated.
    """

    def __init__(
        self,
        inner: Strategy,
        vol_window: int = 20,
        lookback: int = 504,
        percentile_cap: float = 0.90,
    ) -> None:
        if not 0 < percentile_cap <= 1:
            raise ValueError("percentile_cap must be in (0, 1]")
        if vol_window < 2 or lookback < vol_window:
            raise ValueError("need lookback >= vol_window >= 2")
        self.inner = inner
        self.percentile_cap = float(percentile_cap)
        self.lookback = lookback
        self.vol_window = vol_window
        self._vol = RealizedVolCalculator(window=vol_window)
        self._history: deque[float] = deque(maxlen=lookback)
        self.vetoed = 0
        self.allowed = 0

    @property
    def requires_intraday_flatten(self) -> bool:  # type: ignore[override]
        return bool(getattr(self.inner, "requires_intraday_flatten", False))

    def on_bar(self, bar: Any, position_state: Any) -> Signal:
        close = float(getattr(bar, "close", getattr(bar, "ltp", 0.0)))
        current = self._vol.update(close)

        signal = self.inner.on_bar(bar, position_state)

        # The threshold is built from bars STRICTLY BEFORE this one: appending
        # first would let today's own volatility set the bar it has to clear.
        threshold = self._threshold()
        if current is not None:
            self._history.append(current)

        if signal.action != SignalAction.BUY:
            return signal
        if current is None or threshold is None or current <= threshold:
            self.allowed += 1
            return signal

        self.vetoed += 1
        return Signal(action=SignalAction.HOLD, risk_state=signal.risk_state)

    def _threshold(self) -> Optional[float]:
        """The `percentile_cap` quantile of trailing vol, or None until there
        is enough history to mean anything."""
        # A cap of 1.0 means "off". Without this it would resolve to the MAX
        # of trailing vol, and any genuinely new extreme would still be
        # rejected -- so the parameter could never actually be turned off,
        # which makes the whole grid dishonest (there'd be no true control).
        if self.percentile_cap >= 1.0:
            return None
        if len(self._history) < self.vol_window * 2:
            return None
        ordered = sorted(self._history)
        idx = min(int(self.percentile_cap * len(ordered)), len(ordered) - 1)
        return ordered[idx]

    def debug_state(self) -> dict:
        state = dict(self.inner.debug_state() or {})
        state["realized_vol"] = self._vol.value
        state["vol_threshold"] = self._threshold()
        return state

    def get_state_snapshot(self) -> dict:
        return {"inner": self.inner.get_state_snapshot()}

    def load_state_snapshot(self, snapshot: dict) -> None:
        self.inner.load_state_snapshot((snapshot or {}).get("inner") or {})


def build_vol_filtered(params: dict) -> Strategy:
    """Registry entrypoint. `inner_strategy`/`inner_params` name what to wrap;
    everything else is a VolFilteredStrategy kwarg."""
    from growmore_bot.strategies.registry import build_strategy

    params = dict(params)
    name = params.pop("inner_strategy", None)
    if not name:
        raise ValueError("vol_filtered requires an `inner_strategy`")
    inner = build_strategy(name, params.pop("inner_params", {}) or {})
    return VolFilteredStrategy(inner, **params)
