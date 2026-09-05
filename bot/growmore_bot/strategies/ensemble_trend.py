"""Multi-lookback trend ensemble: vote across several MACD speeds at once.

The case for this is statistical rather than technical. `run_all` sweeps many
parameter variants and the results doc reports the best one -- but a deflated
Sharpe calculation over the real sweep (see docs/technical-debt.md) says 192
backtests are only ~15 effective trials, that the luckiest of 15 would post
Sharpe ~0.97 by chance, and that nothing in the sweep clears the conventional
0.95 significance bar. Every parameter variant added makes that worse, not
better.

An ensemble attacks the problem from the other side: instead of choosing the
lookback that happened to win on this five-year window, run several and act
on their agreement. There is no parameter to select, so there is no selection
to be biased by, and the external literature is consistent that averaging
across trend speeds is more robust out of sample than any single speed.

Two things to be clear about before reading its backtest:

  * **It will almost certainly score WORSE in-sample than the luckiest single
    variant.** That is the intended behaviour, not a regression -- the point
    is to give up the lucky tail in exchange for not depending on which
    lookback was lucky. Judge it on out-of-sample and deflated-Sharpe terms.
  * **The vote is binary, not fractional.** "3 of 5 agree" cannot be
    expressed as 0.6 lots when the minimum tradeable size is one whole lot
    (see growmore_bot.risk.sizing and research/capital/admission.py), so the
    ensemble is long when at least `min_agreement` members are bullish and
    flat otherwise. Fractional conviction becomes meaningful only once
    position sizing has room to move.

State handling follows RegimeSwitchStrategy: every member sees every bar, so
their internal EMAs stay correct whether or not their vote is currently
decisive.
"""
from __future__ import annotations

from typing import Any, Optional

from growmore_bot.strategies.base import Signal, SignalAction, Strategy
from growmore_bot.strategies.macd_trend import MacdTrendStrategy

#: Deliberately spread across fast, medium and slow rather than clustered --
#: three near-identical lookbacks would agree almost always and add nothing.
DEFAULT_SPEEDS: list[tuple[int, int, int]] = [
    (5, 13, 5),
    (8, 21, 7),
    (12, 26, 9),
    (19, 39, 13),
    (26, 52, 18),
]


class EnsembleTrendStrategy(Strategy):
    def __init__(
        self,
        speeds: Optional[list] = None,
        min_agreement: Optional[int] = None,
    ) -> None:
        raw = DEFAULT_SPEEDS if speeds is None else speeds
        self._speeds = [tuple(s) for s in raw]
        if not self._speeds:
            raise ValueError("at least one speed is required")
        # Simple majority by default.
        self.min_agreement = min_agreement or (len(self._speeds) // 2 + 1)
        if not 1 <= self.min_agreement <= len(self._speeds):
            raise ValueError("min_agreement must be between 1 and the number of speeds")
        self._members = [
            MacdTrendStrategy(fast_period=f, slow_period=s, signal_period=g)
            for f, s, g in self._speeds
        ]
        # Each member's own latest bullish/bearish stance, read from its
        # STATE (macd vs its signal line) rather than from the BUY/SELL
        # events it emits. That distinction is load-bearing: a MACD member
        # only emits on a crossing, so in a smooth sustained trend it crosses
        # once before the ensemble is warm and then stays silent forever --
        # deriving votes from events left every member permanently
        # abstaining and the ensemble permanently flat.
        self._stance: list[Optional[bool]] = [None] * len(self._members)
        self._prev_bullish: Optional[bool] = None

    def on_bar(self, bar: Any, position_state: Any) -> Signal:
        for i, member in enumerate(self._members):
            member.on_bar(bar, position_state)
            state = member.debug_state()
            macd, signal_line = state.get("macd"), state.get("signal")
            if macd is not None and signal_line is not None:
                self._stance[i] = macd > signal_line

        decided = [s for s in self._stance if s is not None]
        if len(decided) < self.min_agreement:
            # Not enough members have formed a view yet (still warming up).
            return Signal(action=SignalAction.HOLD)

        bullish_votes = sum(1 for s in decided if s)
        bullish = bullish_votes >= self.min_agreement

        prev = self._prev_bullish
        self._prev_bullish = bullish
        if prev is None:
            # First decidable bar -- establish the reference, never trade on
            # it, exactly as every other crossing strategy here does.
            return Signal(action=SignalAction.HOLD)
        if bullish and not prev:
            return Signal(action=SignalAction.BUY)
        if not bullish and prev:
            return Signal(action=SignalAction.SELL)
        return Signal(action=SignalAction.HOLD)

    def debug_state(self) -> dict[str, Optional[float]]:
        decided = [s for s in self._stance if s is not None]
        return {
            "bullish_votes": float(sum(1 for s in decided if s)),
            "votes_cast": float(len(decided)),
            "votes_needed": float(self.min_agreement),
            "members": float(len(self._members)),
        }

    def get_state_snapshot(self) -> dict[str, Any]:
        if all(s is None for s in self._stance) and self._prev_bullish is None:
            return {}
        return {"stance": list(self._stance), "prev_bullish": self._prev_bullish}

    def load_state_snapshot(self, snapshot: dict[str, Any]) -> None:
        stance = snapshot.get("stance")
        if isinstance(stance, list) and len(stance) == len(self._stance):
            self._stance = list(stance)
        if "prev_bullish" in snapshot:
            self._prev_bullish = snapshot["prev_bullish"]


__all__ = ["EnsembleTrendStrategy", "DEFAULT_SPEEDS"]
