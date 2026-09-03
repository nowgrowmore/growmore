"""AlwaysFlip -- a demo/smoke-test strategy, not a real trading strategy.

Every real strategy in this package waits for a real market condition
(a crossover, a threshold breach) before it ever signals, which is correct
for trading but makes it hard to *watch* the paper-trading pipeline (fills,
lot-scaled P&L, position open/close, logging) actually execute on demand.
This strategy exists only to force that: BUY whenever there's no open
position, SELL whenever there is one -- deterministic regardless of price,
so a single enabled bot_config using it is guaranteed to produce a BUY on
its next tick and a SELL on the tick after that. Never intended to be run
against real capital.
"""
from __future__ import annotations

from typing import Any, Optional

from growmore_bot.strategies.base import Signal, SignalAction, Strategy


class AlwaysFlipStrategy(Strategy):
    def __init__(self) -> None:
        self._last_close: Optional[float] = None

    def on_bar(self, bar: Any, position_state: Any) -> Signal:
        self._last_close = float(bar.close)
        if position_state is None:
            return Signal(action=SignalAction.BUY)
        return Signal(action=SignalAction.SELL)

    def debug_state(self) -> dict[str, Optional[float]]:
        return {"last_close": self._last_close}


__all__ = ["AlwaysFlipStrategy"]
