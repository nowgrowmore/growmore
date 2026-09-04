"""Strategy interface shared by backtest and paper-trading engines.

A strategy is a pure function of (bar, position_state) -> Signal. It must not
reach out to the network, the DB, or the Dhan client directly -- the engines
(backtest and paper) are responsible for feeding it data and acting on its
signals, which keeps strategies trivially unit-testable with hand-constructed
price series.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class SignalAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class Signal:
    action: SignalAction
    size: Optional[float] = None


class Strategy(ABC):
    """Base class for all strategies. Subclasses must implement `on_bar`."""

    @abstractmethod
    def on_bar(self, bar: Any, position_state: Any) -> Signal:
        """Given the latest bar and current position state, return a Signal.

        `bar` is expected to expose at least `.open/.high/.low/.close` (see
        growmore_bot.broker.dhan_client.Bar for the historical-data shape, and
        growmore_bot.broker.dhan_client.Quote for the live shape -- both are
        duck-typed by strategies, not required base classes).

        `position_state` is opaque to the base class; each engine decides what
        it passes (e.g. None, "no position", or a dict/dataclass with
        quantity + avg_entry_price).
        """
        raise NotImplementedError

    def debug_state(self) -> dict[str, Optional[float]]:
        """The strategy's current computed indicator values (e.g. MACD/signal
        line, fast/slow SMA), for logging/observability -- so a log line can
        show *why* a signal did or didn't fire, not just that it didn't.
        Default empty; each strategy overrides with whatever it tracks.
        Never called from `on_bar` itself -- purely a read-only snapshot for
        callers like PaperTradingEngine.
        """
        return {}

    def get_state_snapshot(self) -> dict[str, Any]:
        """Whatever internal "previous value" state this strategy needs to
        correctly resume crossing/threshold-recovery detection on the NEXT
        live tick. Empty dict (default) for strategies that don't need this.

        Why this exists: `growmore_bot.scheduler.run` builds a fresh
        strategy instance every tick and replays historical bars up to
        yesterday's close (`_warm_up_strategy`) before evaluating today's
        live quote -- so without restoring this snapshot, a strategy's
        "previous value" for crossing detection is always yesterday's
        close, not the last time it was actually checked. A signal meant to
        fire exactly once at a real crossing would instead re-fire on every
        tick for the rest of the day the live value stays past the
        threshold, since every tick compares against the same fixed
        (yesterday's) baseline. Found live 2026-09-04: a real position's
        unrealized P&L stayed stuck because the strategy kept re-signalling
        BUY (correctly rejected by the max_position_size guard, but that
        code path skips mark-to-market) instead of correctly reporting HOLD.
        """
        return {}

    def load_state_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Restore state saved by `get_state_snapshot` from the last LIVE
        tick. Called (by `growmore_bot.scheduler.run`) after historical
        warm-up completes but before evaluating the live quote. Must ignore
        an empty dict or unknown keys rather than raising -- the first tick
        ever for a config has no prior snapshot to restore.
        """
        return None

    requires_intraday_flatten: bool = False
    """Set True on a strategy whose logic is inherently single-day (e.g. it
    trades off a level that resets every session, like a live VWAP or a
    prior-day pivot range) -- a position it opens shouldn't be allowed to
    carry into a new day, where that context has already reset to something
    else. Checked by `growmore_bot.scheduler.run` alongside the existing
    contract-expiry close-out cutoff to force-flatten near the daily MCX
    session close. Default False: nearly every strategy here is a
    multi-day swing strategy for which this would be actively wrong."""


__all__ = ["Strategy", "Signal", "SignalAction"]
