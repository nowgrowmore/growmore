"""Session replay: backtest an intraday strategy the way the scheduler runs it.

`VwapSessionBounceStrategy` has been enabled in paper trading with NO
backtest at all, on the documented grounds that its live session VWAP "has no
equivalent in historical bars". With intraday bars that is no longer true, so
this exists to settle whether it should stay enabled.

The replay mirrors `scheduler/run.py` deliberately, because a backtest of a
different system would be worse than none:

  1. At session start, feed the PREVIOUS session aggregated into a daily bar
     with NO `.ltp` attribute -- which makes the strategy take its warm-up
     branch and set today's CPR from yesterday's range, exactly as
     `_warm_up_strategy` does.
  2. Then feed each intraday bar quote-shaped (`ltp`, `vwap`, high/low/close)
     with the running session VWAP, which is the live branch.
  3. Reset the crossing reference at session start, mirroring the
     `stale_intraday_state` guard -- VWAP and CPR are single-day concepts.
  4. Force-flatten near the session close, which the live engines do via
     `force_close_end_of_day` and `backtest/engine.py` models not at all.

Costs are charged per leg with the real MCX model. That matters far more here
than for a daily book: the same round trip that costs a daily strategy 0.3
percentage points of CAGR over five years is paid on every intraday signal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace
from typing import Optional

import pandas as pd

from growmore_bot.costs import CostModel, leg_cost, slippage_price
from growmore_bot.strategies.base import SignalAction
from research.intraday.sessions import daily_bar, running_session_vwap, sessions

#: Flatten this many bars before the session's last, mirroring the live
#: `is_near_session_close(buffer_minutes=15)` at 5-minute resolution.
FLATTEN_BARS_BEFORE_CLOSE = 3


@dataclass
class IntradayTrade:
    session: date
    entry_price: float
    exit_price: float
    pnl: float
    reason: str


@dataclass
class ReplayResult:
    trades: list[IntradayTrade] = field(default_factory=list)
    sessions_replayed: int = 0
    signals_by_session: list[int] = field(default_factory=list)

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def signals_per_session(self) -> float:
        if not self.signals_by_session:
            return 0.0
        return sum(self.signals_by_session) / len(self.signals_by_session)


def _quote(row, vwap: Optional[float]) -> SimpleNamespace:
    """The live shape: has `ltp`, which no historical Bar does -- that is how
    the strategy tells a live quote from a warm-up bar."""
    return SimpleNamespace(
        ltp=float(row.close), vwap=vwap, open=float(row.open),
        high=float(row.high), low=float(row.low), close=float(row.close),
        volume=float(row.volume),
    )


def replay(
    build_strategy,
    frame: pd.DataFrame,
    lot_size: int,
    tick_size: float,
    cost_model: Optional[CostModel] = None,
) -> ReplayResult:
    result = ReplayResult()
    previous: Optional[pd.DataFrame] = None

    for session_date, bars in sessions(frame):
        if previous is None:
            previous = bars
            continue

        strategy = build_strategy()
        # Warm-up: yesterday's session as a daily bar, no `ltp` -> CPR branch.
        strategy.on_bar(SimpleNamespace(**daily_bar(previous)), None)

        vwaps = running_session_vwap(bars)
        position: Optional[float] = None
        signals = 0
        last_index = len(bars) - 1

        for i, row in enumerate(bars.itertuples()):
            vwap = vwaps.iloc[i]
            vwap = None if pd.isna(vwap) else float(vwap)
            state = None if position is None else {"quantity": 1.0, "avg_entry_price": position}
            signal = strategy.on_bar(_quote(row, vwap), state)

            forced = i >= last_index - FLATTEN_BARS_BEFORE_CLOSE
            price = float(row.close)

            if signal.action != SignalAction.HOLD:
                signals += 1

            if position is None and signal.action == SignalAction.BUY and not forced:
                position = _fill(price, "buy", tick_size, cost_model)
            elif position is not None and (signal.action == SignalAction.SELL or forced):
                exit_price = _fill(price, "sell", tick_size, cost_model)
                gross = (exit_price - position) * lot_size
                charges = _charges(position, exit_price, lot_size, cost_model)
                result.trades.append(
                    IntradayTrade(
                        session=session_date, entry_price=position, exit_price=exit_price,
                        pnl=gross - charges,
                        reason="end_of_day" if forced else "signal",
                    )
                )
                position = None

        result.sessions_replayed += 1
        result.signals_by_session.append(signals)
        previous = bars

    return result


def _fill(price: float, side: str, tick_size: float, model: Optional[CostModel]) -> float:
    if model is None:
        return price
    return slippage_price(price, side, tick_size, model)  # type: ignore[arg-type]


def _charges(entry: float, exit_price: float, lot_size: int, model: Optional[CostModel]) -> float:
    if model is None:
        return 0.0
    return leg_cost(entry * lot_size, "buy", model) + leg_cost(exit_price * lot_size, "sell", model)


__all__ = ["replay", "ReplayResult", "IntradayTrade", "FLATTEN_BARS_BEFORE_CLOSE"]
