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


__all__ = ["Strategy", "Signal", "SignalAction"]
