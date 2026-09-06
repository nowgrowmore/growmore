"""Strike-selection logic shared by the offline wheel research and the live
wheel-basket paper-trading engine.

Lives here, not in research/stock_options/wheel_engine.py, so the live engine
(growmore_bot/wheel_basket/) never needs to import from research/ -- every
other shared-code precedent in this repo runs research -> growmore_bot
(costs.py, backtest/engine.py, risk/sizing.py), never the reverse.
research/stock_options/wheel_engine.py imports and re-exports these names
unchanged, so its own public API and existing tests are unaffected by the
move.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

#: A strike must have actually printed to be sellable. Bhavcopy/live chains
#: list every strike the exchange offers, including ones that never traded.
MIN_STRIKE_VOLUME = 1


def select_strike(
    day_chain: pd.DataFrame,
    opt_type: str,
    spot: float,
    target_otm: float,
    floor_strike: Optional[float] = None,
) -> Optional[pd.Series]:
    """The tradeable strike closest to `target_otm` away from spot.

    `floor_strike` enforces the "never write a call below the assignment
    basis" rule (optionally raised by an RSI-scaled buffer, see
    `rsi_scaled_basis_buffer`): candidates below it are removed entirely
    rather than penalised, because the rule is absolute.
    """
    side = day_chain[
        (day_chain["opt_type"] == opt_type)
        & (day_chain["volume"] >= MIN_STRIKE_VOLUME)
        & (day_chain["settle"] > 0)
    ]
    if side.empty:
        return None
    target = spot * (1 - target_otm) if opt_type == "PE" else spot * (1 + target_otm)
    if floor_strike is not None:
        side = side[side["strike"] >= floor_strike]
        if side.empty:
            return None
    idx = (side["strike"] - target).abs().idxmin()
    return side.loc[idx]


#: RSI(14) -> how far above the assignment basis to raise the no-loss floor.
#: Checked in descending order, first threshold the RSI clears wins. Real
#: momentum (>=60) earns the full "couple of percent" buffer headroom to
#: capture more of a genuine recovery; neutral readings get a token buffer;
#: below 40 there is no shown strength to justify giving up any premium for,
#: so it falls back to exactly the plain basis-floor rule.
RSI_BASIS_BUFFER_TIERS: tuple[tuple[float, float], ...] = (
    (60.0, 0.05),
    (40.0, 0.02),
    (0.0, 0.0),
)


def rsi_scaled_basis_buffer(rsi: Optional[float]) -> float:
    if rsi is None:
        return 0.0
    for threshold, pct in RSI_BASIS_BUFFER_TIERS:
        if rsi >= threshold:
            return pct
    return 0.0


__all__ = [
    "MIN_STRIKE_VOLUME",
    "select_strike",
    "RSI_BASIS_BUFFER_TIERS",
    "rsi_scaled_basis_buffer",
]
