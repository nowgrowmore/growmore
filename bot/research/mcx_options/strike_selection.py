"""Target-delta strike selection for MCX commodity options.

Separate from `growmore_bot.options.strike_selection` (which picks a strike
by a fixed OTM-distance from spot for NSE-style equity options and stays
untouched by this module). MCX commodity options are priced off the FUTURES
contract (Black-76, see `pricing.py`), and this engine's regime logic
(`regime.py`) dictates a TARGET DELTA rather than a target OTM distance, so
the selection rule here is genuinely different, not just a renamed copy.

**Implied-vol-with-fallback, documented plainly**: for each candidate strike
this first tries to back out that day's implied vol from its own settlement
price via `implied_vol_b76`. MCX bhavcopy rows sometimes carry stale, zero,
or otherwise unusable settlement prices (see `bhavcopy.py`'s caveats), and
`implied_vol_b76` already refuses (returns `None`) rather than guessing in
those cases. When it refuses, this module falls back to the caller-supplied
`sigma` (expected to be something like `pricing.realised_vol` computed from
the underlying futures series) for THAT STRIKE ONLY -- other strikes in the
same chain still use their own solved IV where available. This is a
deliberate per-strike fallback, not a chain-wide one: it keeps one bad row
from throwing away every other strike's real market-implied delta.
"""
from __future__ import annotations

from typing import Optional

from research.mcx_options.bhavcopy import MCXOptionRow
from research.mcx_options.pricing import black76_delta, implied_vol_b76


def select_strike_by_target_delta(
    chain: list[MCXOptionRow],
    futures_price: float,
    T_years: float,
    sigma: float,
    r: float,
    target_delta: float,
    min_open_interest: int,
) -> Optional[MCXOptionRow]:
    """The chain member whose |delta| is closest to `target_delta`.

    `chain` must be all the same trade day/expiry/opt_type -- this function
    does not filter on those fields, it only ranks and OI-filters what it is
    given. Strikes with `open_interest < min_open_interest` are dropped
    before ranking; `None` is returned when nothing survives that filter.
    """
    survivors = [row for row in chain if row.open_interest >= min_open_interest]
    if not survivors:
        return None

    best_row: Optional[MCXOptionRow] = None
    best_gap = float("inf")
    for row in survivors:
        iv = implied_vol_b76(row.opt_type, row.close, futures_price, row.strike, T_years, r)
        vol = iv if iv is not None else sigma
        delta = black76_delta(row.opt_type, futures_price, row.strike, T_years, vol, r)
        gap = abs(abs(delta) - target_delta)
        if gap < best_gap:
            best_gap = gap
            best_row = row
    return best_row


__all__ = ["select_strike_by_target_delta"]
