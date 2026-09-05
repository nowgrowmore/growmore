"""Capital, notional and leverage arithmetic.

Motivation (found via the strategy review, 2026-09-04): `BacktestEngine`
trades exactly 1 lot regardless of instrument, against one flat
`Settings.default_virtual_capital` for the whole sweep. But MCX lot notionals
differ by more than an order of magnitude -- roughly Rs 0.55 lakh for a Crude
Oil Mini lot versus Rs 34 lakh for a Copper lot. So the sweep silently ran
about 0.2x leverage on one and 14x on the other, which makes the CAGR column
a ranking of contract size rather than of edge. These functions exist to make
that explicit and to put a ranking back on an equal-risk footing.

`lot_size` is always QUOTE UNITS per lot, never raw grams/kg -- see
`growmore_bot.config.CommodityPlaceholder`. Gold Mini is a 100g contract
quoted per 10g, so its lot_size is 10 and one lot's notional is
`price_per_10g * 10`.
"""
from __future__ import annotations


def notional_per_lot(price: float, lot_size: int) -> float:
    """Rupee value of one lot at `price` (the quoted price per quote unit)."""
    if price <= 0:
        raise ValueError("price must be positive")
    if lot_size <= 0:
        raise ValueError("lot_size must be positive")
    return float(price) * float(lot_size)


def leverage_at_1_lot(capital: float, price: float, lot_size: int) -> float:
    """How much leverage holding a SINGLE lot represents against `capital`.

    1.0 means one lot is exactly the account. Below 1.0 the account can't
    put its capital to work at all through this instrument; well above 1.0
    the "1 lot per signal" convention is quietly running a leveraged book.
    """
    if capital <= 0:
        raise ValueError("capital must be positive")
    return notional_per_lot(price, lot_size) / float(capital)


__all__ = ["notional_per_lot", "leverage_at_1_lot"]
