"""Pre-expiry close-out guard, mirroring Dhan's own real MCX delivery rules.

Verified against Dhan's Risk Management Policy and settlement-policy support
article (2026-09-03): Dhan does NOT permit physical delivery for retail
clients on any MCX commodity contract. For compulsory-delivery contracts,
Dhan force-squares-off all open positions "post 11:00 AM on the trading day
prior to the commencement of the Tender Period", and blocks fresh position
creation from that same point. The Tender Period itself starts 5 trading
days before expiry for bullion (Gold Mini, Silver Mini), 3 trading days
before expiry for base metals (Copper, Zinc Mini, Nickel, Aluminium Mini,
Lead Mini). Crude Oil Mini is cash-settled against a reference price -- no
delivery obligation, so Dhan applies no forced close-out to it, and neither
do we.

This module computes our OWN cutoff -- deliberately earlier than Dhan's, via
`DEFAULT_SAFETY_BUFFER_TRADING_DAYS` -- so the bot always acts well before
Dhan would force the issue, and to absorb the imprecision of approximating
"trading days" as plain weekdays (no MCX holiday calendar exists yet; see
docs/technical-debt.md item #1). Every unaccounted holiday only pushes our
already-conservative cutoff earlier still, never later.

`DELIVERY_CATEGORY` mirrors the symbol set in
`growmore_bot.config.DEFAULT_COMMODITY_UNIVERSE` -- adding a new commodity
there also requires adding it here, or it silently gets no close-out guard
(safe-by-default, but worth remembering).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

DELIVERY_CATEGORY: dict[str, str] = {
    "GOLDM": "bullion",
    "SILVERM": "bullion",
    "COPPER": "base_metal",
    "ZINCMINI": "base_metal",
    "NICKEL": "base_metal",
    "ALUMINI": "base_metal",
    "LEADMINI": "base_metal",
    "CRUDEOILM": "cash",
}

# Trading days before expiry that MCX's Tender Period begins, per category.
_TENDER_PERIOD_TRADING_DAYS: dict[str, int] = {
    "bullion": 5,
    "base_metal": 3,
}

DEFAULT_SAFETY_BUFFER_TRADING_DAYS = 2


def _subtract_trading_days(d: date, trading_days: int) -> date:
    """Step back `trading_days` weekdays (Mon-Fri) from `d`."""
    current = d
    remaining = trading_days
    while remaining > 0:
        current -= timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def close_out_cutoff_date(
    symbol: str,
    contract_expiry: Optional[date],
    safety_buffer_trading_days: int = DEFAULT_SAFETY_BUFFER_TRADING_DAYS,
) -> Optional[date]:
    """The date (inclusive) on/after which this instrument's open positions
    must be force-closed and no new positions opened.

    Returns None when there's no delivery-driven close-out risk: an unknown
    symbol, a cash-settled one (e.g. Crude Oil Mini), or a missing
    `contract_expiry`.
    """
    if contract_expiry is None:
        return None
    category = DELIVERY_CATEGORY.get(symbol)
    if category is None:
        return None
    tender_days = _TENDER_PERIOD_TRADING_DAYS.get(category)
    if tender_days is None:
        return None  # cash-settled category (e.g. "cash") -- no close-out applies.

    # Dhan squares off 1 trading day before the Tender Period starts.
    dhan_cutoff = _subtract_trading_days(contract_expiry, tender_days + 1)
    return _subtract_trading_days(dhan_cutoff, safety_buffer_trading_days)


def is_past_close_out_cutoff(
    symbol: str,
    contract_expiry: Optional[date],
    today: date,
    safety_buffer_trading_days: int = DEFAULT_SAFETY_BUFFER_TRADING_DAYS,
) -> bool:
    cutoff = close_out_cutoff_date(symbol, contract_expiry, safety_buffer_trading_days)
    if cutoff is None:
        return False
    return today >= cutoff


__all__ = ["DELIVERY_CATEGORY", "close_out_cutoff_date", "is_past_close_out_cutoff"]
