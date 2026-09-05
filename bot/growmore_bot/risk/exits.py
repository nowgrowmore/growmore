"""Pure stop-placement arithmetic.

Nothing here knows about bars, positions or engines -- it is float in, float
out, so every rule is checkable by hand in a test. All the genuinely
difficult parts of the risk layer are about WHEN these get called and
against which bar's data; that lives in growmore_bot.risk.wrapper and in
BacktestEngine's intrabar ordering.

`direction` is +1 for a long position and -1 for a short, matching the sign
convention used for position quantity.

Every function returns None rather than a number when ATR isn't available
yet (during warm-up). That matters: falling back to the entry price would
place a stop exactly at the fill and exit on the first adverse tick, and
falling back to "no stop" silently would be worse -- the caller has to
decide explicitly.
"""
from __future__ import annotations

from typing import Optional


def _is_sane(stop: float) -> bool:
    """A stop at or below zero is not a stop -- no instrument trades there,
    so the level can only be reached by corrupt data or a nonsensically large
    ATR. Defence in depth behind the feed validation in
    `growmore_bot.broker.dhan_client`: a run of zero-priced NICKEL bars once
    produced an ATR large enough to place a stop at -0.4, which then "filled"
    and booked a Rs 485,199 loss on a single trade. Returning None here means
    "no protective stop this bar", which is the honest answer when the inputs
    are junk.
    """
    return stop > 0


def initial_atr_stop(
    entry_price: float, atr: Optional[float], k: float, direction: int
) -> Optional[float]:
    """The protective stop placed at entry: `k` ATRs adverse of the fill."""
    if k <= 0:
        raise ValueError("k must be positive")
    if direction not in (1, -1):
        raise ValueError("direction must be +1 (long) or -1 (short)")
    if atr is None:
        return None
    stop = entry_price - direction * k * atr
    return stop if _is_sane(stop) else None


def chandelier_stop(
    high_water: float, atr: Optional[float], k: float, direction: int
) -> Optional[float]:
    """Chuck LeBeau's Chandelier exit: `k` ATRs back from the best price the
    trade has seen (the highest high for a long, the lowest low for a short).

    The caller is responsible for two things this function cannot enforce:
    the high-water mark must come from CLOSED bars only (using the forming
    bar's high is lookahead), and the resulting stop must be ratcheted so it
    never loosens.
    """
    if k <= 0:
        raise ValueError("k must be positive")
    if direction not in (1, -1):
        raise ValueError("direction must be +1 (long) or -1 (short)")
    if atr is None:
        return None
    stop = high_water - direction * k * atr
    return stop if _is_sane(stop) else None


def time_stop_hit(bars_held: int, max_bars: Optional[int]) -> bool:
    """True once a position has been held for `max_bars` bars. `None`
    disables the time stop entirely, which is the default -- most strategies
    here are multi-day swing systems with no natural holding limit."""
    if max_bars is None:
        return False
    return bars_held >= max_bars


__all__ = ["initial_atr_stop", "chandelier_stop", "time_stop_hit"]
