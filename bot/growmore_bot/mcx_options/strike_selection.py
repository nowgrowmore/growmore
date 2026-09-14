"""Target-delta strike selection for the LIVE MCX Goldmini/Silvermini
options-selling engine, over a live `OptionChainSnapshot`
(growmore_bot.broker.dhan_client).

Live analog of `research/mcx_options/strike_selection.py` -- same contract
(rank candidates by |delta - target_delta| after an OI floor, Black-76
delta), freshly written against this repo's own `OptionChainRow`/
`OptionChainSnapshot` dataclasses rather than the offline backtest's
`MCXOptionRow` (bhavcopy-shaped).

**IV, live vs. backtest**: Dhan's real-time option chain already quotes each
strike's own implied vol (`OptionChainRow.iv`, a fraction, None when Dhan
has no quote for that strike) -- there is no raw settlement price to bisect
an IV out of the way `research/mcx_options/strike_selection.py` must (MCX
bhavcopy rows carry settlement prices, not IV). This module therefore uses
`row.iv` directly when present, and falls back to the caller-supplied
`sigma` (expected to be something like a realised-vol estimate from the
underlying futures series) ONLY for a strike whose IV is missing -- the same
per-strike (not chain-wide) fallback discipline the research module
documents, so one illiquid strike never throws away every other strike's
real market-implied delta.
"""
from __future__ import annotations

from typing import Optional

from growmore_bot.broker.dhan_client import OptionChainRow, OptionChainSnapshot
from growmore_bot.mcx_options.pricing import black76_delta


def select_strike_by_target_delta(
    chain: OptionChainSnapshot,
    opt_type: str,
    futures_price: float,
    T_years: float,
    sigma: float,
    r: float,
    target_delta: float,
    min_open_interest: int,
) -> Optional[OptionChainRow]:
    """The chain row (of `opt_type`) whose |delta| is closest to
    `target_delta`. Rows of the other `opt_type`, or with
    `oi < min_open_interest`, are excluded before ranking. `None` when
    nothing survives (empty chain, no matching side, or every row filtered
    out by the OI floor).
    """
    survivors = [
        row for row in chain.rows if row.opt_type == opt_type and row.oi >= min_open_interest
    ]
    if not survivors:
        return None

    best_row: Optional[OptionChainRow] = None
    best_gap = float("inf")
    for row in survivors:
        vol = row.iv if row.iv is not None else sigma
        delta = black76_delta(opt_type, futures_price, row.strike, T_years, vol, r)
        gap = abs(abs(delta) - target_delta)
        if gap < best_gap:
            best_gap = gap
            best_row = row
    return best_row


__all__ = ["select_strike_by_target_delta"]
