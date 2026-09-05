"""RiskManagedStrategy -- adds stops to any existing strategy, by wrapping it.

Design note, because the alternative is tempting and wrong. The obvious move
is to put stop logic inside each strategy, or to teach the engines about
stops directly. Both were rejected:

  * The exit logic is IDENTICAL for all eight strategies and orthogonal to
    all eight entry rules. Writing it eight times is how it drifts.
  * This repo already measured what happens when entry-signal complexity is
    added for its own sake: every `regime_switch` variant scored worse than
    the standalone MACD it wrapped. Composition is fine; entangling entry
    and exit logic is what hurt.
  * `RegimeSwitchStrategy` already proves the composition pattern end to end,
    including the awkward parts (snapshot delegation, debug_state merging).
    This copies that shape.
  * A wrapper that IS a Strategy drops into all six registration sites with
    no engine changes at all.

State ownership, which is the subtle part. This object holds ONLY indicator
state (ATR, and the running extreme). It holds NO position state. That is
deliberate: `scheduler.run._warm_up_strategy` replays months of history with
`position_state=None` on every tick, so anything position-derived kept in
the instance would be garbage by the time the live quote arrives. Per-trade
risk state (stop price, high-water mark, bars held) instead travels on the
Signal as `risk_state`, is persisted by the engine, and comes back in
`position_state["risk"]` on the next bar.

Exit precedence, once in a position: stop, then trail, then time, then --
only if none fired -- the inner strategy's own opinion. A protective stop
that loses to a "hold" from the inner strategy isn't a protective stop.
"""
from __future__ import annotations

from typing import Any, Optional

from growmore_bot.indicators import AtrCalculator
from growmore_bot.risk.exits import chandelier_stop, initial_atr_stop, time_stop_hit
from growmore_bot.strategies.base import Signal, SignalAction, Strategy


class RiskManagedStrategy(Strategy):
    def __init__(
        self,
        inner: Strategy,
        atr_period: int = 14,
        initial_stop_atr: float = 2.0,
        trail_atr: Optional[float] = 3.0,
        max_bars_held: Optional[int] = None,
    ) -> None:
        if initial_stop_atr <= 0:
            raise ValueError("initial_stop_atr must be positive")
        if trail_atr is not None and trail_atr <= 0:
            raise ValueError("trail_atr must be positive or None")
        self.inner = inner
        self.initial_stop_atr = initial_stop_atr
        self.trail_atr = trail_atr
        self.max_bars_held = max_bars_held
        self._atr = AtrCalculator(period=atr_period)
        self._last_close: Optional[float] = None

    # A single-day inner strategy stays single-day when wrapped.
    @property
    def requires_intraday_flatten(self) -> bool:  # type: ignore[override]
        return bool(getattr(self.inner, "requires_intraday_flatten", False))

    def on_bar(self, bar: Any, position_state: Any) -> Signal:
        # ATR updates on EVERY bar, in position or not, so warm-up builds a
        # real value before the first entry ever happens.
        atr = self._atr.update(bar)
        close = float(getattr(bar, "close", getattr(bar, "ltp", 0.0)))
        self._last_close = close

        inner_signal = self.inner.on_bar(bar, position_state)

        risk = (position_state or {}).get("risk") or {} if position_state else {}
        in_position = bool(position_state) and float(
            (position_state or {}).get("quantity") or 0
        ) != 0

        if not in_position:
            if inner_signal.action == SignalAction.BUY:
                return self._open(inner_signal, direction=1, reference=close, atr=atr)
            if inner_signal.action == SignalAction.SELL:
                # The engines are long-only today, so a SELL while flat is a
                # no-op there -- but pass the stop through anyway so this
                # wrapper is already correct when shorting lands.
                return self._open(inner_signal, direction=-1, reference=close, atr=atr)
            return inner_signal

        direction = 1 if float(position_state.get("quantity") or 0) > 0 else -1
        updated = self._advance(risk, bar, close, atr, direction)

        exit_reason = self._exit_reason(updated, bar, direction)
        if exit_reason is not None:
            action = SignalAction.SELL if direction == 1 else SignalAction.BUY
            return Signal(action=action, exit_reason=exit_reason, risk_state=updated)

        # No protective exit fired -- defer to the inner strategy, but keep
        # carrying the updated stop so the engine can persist it.
        return Signal(
            action=inner_signal.action,
            size=inner_signal.size,
            stop_price=updated.get("stop_price"),
            exit_reason="signal" if inner_signal.action != SignalAction.HOLD else None,
            risk_state=updated,
        )

    def _open(self, inner_signal: Signal, direction: int, reference: float, atr: Optional[float]):
        stop = initial_atr_stop(reference, atr, self.initial_stop_atr, direction)
        return Signal(
            action=inner_signal.action,
            size=inner_signal.size,
            stop_price=stop,
            risk_state={
                "stop_price": stop,
                "high_water": reference,
                "entry_atr": atr,
                "bars_held": 0,
                "direction": direction,
            },
        )

    def _advance(
        self, risk: dict, bar: Any, close: float, atr: Optional[float], direction: int
    ) -> dict:
        """Roll the per-trade risk state forward by one CLOSED bar."""
        high = float(getattr(bar, "high", close))
        low = float(getattr(bar, "low", close))
        prior_water = risk.get("high_water")
        water = (
            max(prior_water, high) if direction == 1 and prior_water is not None
            else min(prior_water, low) if direction == -1 and prior_water is not None
            else (high if direction == 1 else low)
        )

        stop = risk.get("stop_price")
        if self.trail_atr is not None:
            trailed = chandelier_stop(water, atr, self.trail_atr, direction)
            if trailed is not None:
                # Ratchet only: a trailing stop must never loosen, or it
                # stops being a stop.
                stop = trailed if stop is None else (
                    max(stop, trailed) if direction == 1 else min(stop, trailed)
                )

        return {
            "stop_price": stop,
            "high_water": water,
            "entry_atr": risk.get("entry_atr", atr),
            "bars_held": int(risk.get("bars_held", 0)) + 1,
            "direction": direction,
        }

    def _exit_reason(self, risk: dict, bar: Any, direction: int) -> Optional[str]:
        if time_stop_hit(int(risk.get("bars_held", 0)), self.max_bars_held):
            return "time"
        stop = risk.get("stop_price")
        if stop is None:
            return None
        low = float(getattr(bar, "low", getattr(bar, "close", 0.0)))
        high = float(getattr(bar, "high", getattr(bar, "close", 0.0)))
        breached = low <= stop if direction == 1 else high >= stop
        if not breached:
            return None
        # "trail" vs "stop" is purely for the trade log's benefit: whether
        # the level that got hit had ever been ratcheted away from entry.
        return "trail" if self.trail_atr is not None else "stop"

    def debug_state(self) -> dict[str, Optional[float]]:
        state = dict(self.inner.debug_state())
        state["atr"] = self._atr.value
        return state

    def get_state_snapshot(self) -> dict[str, Any]:
        return {"inner": self.inner.get_state_snapshot()}

    def load_state_snapshot(self, snapshot: dict[str, Any]) -> None:
        if "inner" in snapshot:
            self.inner.load_state_snapshot(snapshot["inner"])


#: Inner strategies a risk-managed config may wrap, by the same
#: name-in-params pattern RegimeSwitchStrategy already uses for its ranging
#: leg -- so adding stops to a strategy costs one grid entry, not a whole new
#: strategy file plus four dashboard cases.
_INNER_BUILDERS = {
    "sma_crossover": lambda p: __import__(
        "growmore_bot.strategies.sma_crossover", fromlist=["x"]
    ).SmaCrossoverStrategy(**p),
    "donchian_breakout": lambda p: __import__(
        "growmore_bot.strategies.donchian_breakout", fromlist=["x"]
    ).DonchianBreakoutStrategy(**p),
    "rsi_mean_reversion": lambda p: __import__(
        "growmore_bot.strategies.rsi_mean_reversion", fromlist=["x"]
    ).RsiMeanReversionStrategy(**p),
    "macd_trend": lambda p: __import__(
        "growmore_bot.strategies.macd_trend", fromlist=["x"]
    ).MacdTrendStrategy(**p),
    "ensemble_trend": lambda p: __import__(
        "growmore_bot.strategies.ensemble_trend", fromlist=["x"]
    ).EnsembleTrendStrategy(**p),
    "bollinger_reversion": lambda p: __import__(
        "growmore_bot.strategies.bollinger_reversion", fromlist=["x"]
    ).BollingerReversionStrategy(**p),
}


def build_risk_managed(params: dict) -> RiskManagedStrategy:
    """Construct a RiskManagedStrategy from a flat params dict, as stored in
    `strategies.params`:

        {"inner_strategy": "donchian_breakout",
         "inner_params": {"period": 20},
         "atr_period": 14, "initial_stop_atr": 2.0, "trail_atr": 3.0,
         "max_bars_held": null}
    """
    params = dict(params)
    name = params.pop("inner_strategy", None)
    if name not in _INNER_BUILDERS:
        raise ValueError(
            f"Unknown inner_strategy {name!r} -- must be one of {sorted(_INNER_BUILDERS)}"
        )
    inner = _INNER_BUILDERS[name](params.pop("inner_params", {}) or {})
    return RiskManagedStrategy(inner, **params)


__all__ = ["RiskManagedStrategy", "build_risk_managed"]
