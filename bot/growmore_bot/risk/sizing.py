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


def shares_for_capital(price: float, capital: float) -> int:
    """How many shares of a stock at `price` a `capital` budget buys.

    The cash-equity analogue of a futures lot: `BacktestEngine` sizes as
    `qty = (signal.size or 1) * lot_size`, so this count goes in as
    `lot_size` and every stock then runs at ~1x leverage regardless of share
    price -- which is what makes a CAGR column comparable across 210 names
    priced from Rs 15 to Rs 1,50,000.

    `price` must be the FIRST bar's close, never a later one: you capitalise
    an account at the start of the period, and any later price is lookahead.
    That is the same rule `growmore_bot.backtest.run_all.capital_for_run`
    documents for MCX.

    The `max(1, ...)` floor matters. A share costing more than the whole
    budget (MRF trades near Rs 1,50,000) would otherwise round to zero and
    drop out of the study, biasing the universe toward cheap stocks. One
    share is still exactly 1x leverage -- just against a larger account.
    """
    if price <= 0:
        raise ValueError("price must be positive")
    if capital <= 0:
        raise ValueError("capital must be positive")
    return max(1, int(capital // price))


def rounding_drag(price: float, shares: int, capital: float) -> float:
    """Fraction of `capital` left idle because shares are indivisible.

    This is the "1 lot regardless of capital" bias of
    docs/technical-debt.md in its equity form, made measurable. It is
    negligible for cheap stocks and real for expensive ones (3 shares of a
    Rs 1,50,000 stock against a Rs 5,00,000 budget leaves 10% idle, which
    understates that stock's CAGR by a tenth). Clamped at zero, because the
    `max(1, ...)` floor can deploy MORE than the budget -- a bigger account,
    not negative drag.
    """
    if capital <= 0:
        raise ValueError("capital must be positive")
    return max(0.0, 1.0 - (float(price) * shares) / float(capital))


__all__ = [
    "notional_per_lot",
    "leverage_at_1_lot",
    "shares_for_capital",
    "rounding_drag",
]
