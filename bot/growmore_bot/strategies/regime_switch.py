"""ADX-gated regime-switch strategy: routes to a trend-following strategy
(MACD) when the market is trending, and a mean-reversion strategy (RSI, or
a rolling-VWAP+EMA variant) when it's ranging.

Motivation (see docs/goldmini-regime-switch-results.md for the real
backtest): Gold Mini alternates between trending and range-bound stretches
rather than trending persistently -- this bot's own prior sweep found both
MACD Trend and RSI Mean-Reversion scoring well on Gold Mini historically,
presumably at different times. Running either style alone means bleeding
money whenever the market is in the wrong regime for it.

ADX (Average Directional Index, J. Welles Wilder) measures trend
*strength*, not direction -- used here purely as a gate deciding which
sub-strategy is "in control" this tick, never as a signal generator itself.
Thresholds (14-period, enter trending above 25, revert to ranging below 20)
match Wilder's own standard convention, confirmed via real-world gold
trading practice research, not internally invented. The 20-25 band is a
deliberate hysteresis dead zone -- without it, ADX oscillating right at one
bare threshold would flip the regime (and which strategy is trading) back
and forth on every small wiggle, which is exactly the whipsaw risk this
design is trying to reduce, not add to.

`_AdxCalculator` uses the `ewm(alpha=1/period, adjust=False)` smoothing
convention (recursion starts from the very first bar) rather than Wilder's
classical SMA-seeded initialization -- a documented, deliberate choice, not
an oversight: it's the convention most charting libraries actually
implement under the "Wilder smoothing" name, and it's exactly reproducible
via pandas' own `ewm`, which is how this module's test fixtures were
independently verified (Wilder ADX by hand for 20+ bars isn't practical).
"""
from __future__ import annotations

from typing import Any, Optional

from growmore_bot.indicators import AtrCalculator
from growmore_bot.indicators import WilderSmoother as _WilderSmoother
from growmore_bot.strategies.base import Signal, Strategy
from growmore_bot.strategies.macd_trend import MacdTrendStrategy
from growmore_bot.strategies.rsi_mean_reversion import RsiMeanReversionStrategy
from growmore_bot.strategies.vwap_ema_reversion import VwapEmaReversionStrategy


class _AdxCalculator:
    def __init__(self, period: int = 14) -> None:
        self._period = period
        self._prev_bar: Any = None
        # The smoothed True Range this needs for +DI/-DI *is* Wilder ATR --
        # it used to be computed here and thrown away. Sharing one
        # AtrCalculator means ADX and the ATR the risk layer sizes stops
        # from can never drift apart.
        self._atr = AtrCalculator(period)
        self._plus_dm_smoother = _WilderSmoother(period)
        self._minus_dm_smoother = _WilderSmoother(period)
        self._dx_smoother = _WilderSmoother(period)

    def update(self, bar: Any) -> Optional[float]:
        prev = self._prev_bar
        if prev is None:
            # First bar ever: no prior high/low to diff against. Matches the
            # reference calc's NaN-comparisons-are-False +DM/-DM (0); the
            # True range's own first-bar convention lives in
            # growmore_bot.indicators.true_range.
            plus_dm = 0.0
            minus_dm = 0.0
        else:
            up_move = bar.high - prev.high
            down_move = prev.low - bar.low
            plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
            minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        self._prev_bar = bar

        tr_smooth = self._atr.update(bar)
        plus_dm_smooth = self._plus_dm_smoother.update(plus_dm)
        minus_dm_smooth = self._minus_dm_smoother.update(minus_dm)

        if tr_smooth is None or plus_dm_smooth is None or minus_dm_smooth is None or tr_smooth == 0:
            return None

        plus_di = 100 * plus_dm_smooth / tr_smooth
        minus_di = 100 * minus_dm_smooth / tr_smooth
        di_sum = plus_di + minus_di
        dx = 0.0 if di_sum == 0 else 100 * abs(plus_di - minus_di) / di_sum
        return self._dx_smoother.update(dx)

    @property
    def atr(self) -> Optional[float]:
        """Wilder ATR, in price units -- the same smoothed True Range this
        calculator already needs for +DI/-DI. Surfaced so the risk layer can
        size stops off it without recomputing (or, worse, recomputing it with
        a subtly different smoothing convention)."""
        return self._atr.value


_RANGING_STRATEGY_BUILDERS = {
    "rsi": lambda params: RsiMeanReversionStrategy(**params),
    "vwap_ema": lambda params: VwapEmaReversionStrategy(**params),
}


class RegimeSwitchStrategy(Strategy):
    def __init__(
        self,
        ranging_strategy: str,
        macd_params: dict,
        ranging_params: dict,
        adx_period: int = 14,
        adx_trend_enter: float = 25.0,
        adx_trend_exit: float = 20.0,
    ) -> None:
        if ranging_strategy not in _RANGING_STRATEGY_BUILDERS:
            raise ValueError(
                f"Unknown ranging_strategy {ranging_strategy!r} -- must be one of "
                f"{sorted(_RANGING_STRATEGY_BUILDERS)}"
            )
        self._ranging_strategy_name = ranging_strategy
        self._macd = MacdTrendStrategy(**macd_params)
        self._ranging = _RANGING_STRATEGY_BUILDERS[ranging_strategy](ranging_params)
        self._adx = _AdxCalculator(period=adx_period)
        self._adx_trend_enter = adx_trend_enter
        self._adx_trend_exit = adx_trend_exit
        self._regime: Optional[str] = None  # None (not yet decided) | "trending" | "ranging"
        self._last_adx: Optional[float] = None

    def on_bar(self, bar: Any, position_state: Any) -> Signal:
        macd_signal = self._macd.on_bar(bar, position_state)
        ranging_signal = self._ranging.on_bar(bar, position_state)
        adx = self._adx.update(bar)
        self._last_adx = adx

        if adx is not None:
            if adx >= self._adx_trend_enter:
                self._regime = "trending"
            elif adx <= self._adx_trend_exit:
                self._regime = "ranging"
            # else: within the 20-25 dead zone -- keep whatever regime we're already in.

        active_regime = self._regime or "ranging"  # default before ADX is even computable
        return macd_signal if active_regime == "trending" else ranging_signal

    def debug_state(self) -> dict[str, Optional[float]]:
        return {
            "adx": self._last_adx,
            "atr": self._adx.atr,
            "regime": self._regime,
            **self._macd.debug_state(),
            **self._ranging.debug_state(),
        }

    def get_state_snapshot(self) -> dict[str, Any]:
        return {
            "regime": self._regime,
            "macd": self._macd.get_state_snapshot(),
            "ranging": self._ranging.get_state_snapshot(),
        }

    def load_state_snapshot(self, snapshot: dict[str, Any]) -> None:
        if "regime" in snapshot:
            self._regime = snapshot["regime"]
        if "macd" in snapshot:
            self._macd.load_state_snapshot(snapshot["macd"])
        if "ranging" in snapshot:
            self._ranging.load_state_snapshot(snapshot["ranging"])


__all__ = ["RegimeSwitchStrategy"]
