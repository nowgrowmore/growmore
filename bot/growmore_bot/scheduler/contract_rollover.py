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

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional

logger = logging.getLogger(__name__)

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


def roll_to_next_contract(session: Any, dhan_client: Any, instrument: Any, csv_text: str) -> bool:
    """Attempt to automatically roll `instrument` to its next MCX contract
    month, once it's past its close-out cutoff.

    Looks up the next contract from `csv_text` (a fresh download from
    growmore_bot.broker.instrument_master.fetch_instrument_master_csv()),
    then VALIDATES the candidate with a real live quote request before
    committing anything. Refuses to guess -- returns False, leaving
    `instrument` untouched -- when the next contract couldn't be
    unambiguously determined, or the candidate's quote check fails or looks
    implausible (<= 0). On success, updates `instrument.security_id` /
    `instrument.contract_expiry` in place and writes an audit_log entry.

    Falling back to False means the existing manual process still applies:
    someone looks up the next security_id and updates the row by hand (see
    docs/technical-debt.md and docs/pending-actions.md).
    """
    from growmore_bot.broker.instrument_master import find_next_contract
    from growmore_bot.persistence.models import AuditLog

    next_contract = find_next_contract(csv_text, instrument.symbol, instrument.contract_expiry)
    if next_contract is None:
        logger.warning(
            "Could not automatically determine %s's next contract from the instrument master -- "
            "falling back to manual rollover",
            instrument.symbol,
        )
        return False

    candidate = SimpleNamespace(
        id=instrument.id,
        symbol=instrument.symbol,
        exchange_segment=instrument.exchange_segment,
        security_id=next_contract.security_id,
        lot_size=instrument.lot_size,
    )
    try:
        quote = dhan_client.get_quote(candidate)
    except Exception:
        logger.exception(
            "%s's next contract candidate (security_id=%s) failed a live quote check -- not rolling",
            instrument.symbol,
            next_contract.security_id,
        )
        return False
    if quote is None or float(quote.ltp) <= 0:
        logger.warning(
            "%s's next contract candidate (security_id=%s) returned an implausible quote -- not rolling",
            instrument.symbol,
            next_contract.security_id,
        )
        return False

    old_security_id = instrument.security_id
    old_expiry = instrument.contract_expiry
    instrument.security_id = next_contract.security_id
    instrument.contract_expiry = next_contract.expiry
    session.add(instrument)
    session.add(
        AuditLog(
            id=uuid.uuid4(),
            ts=datetime.now(timezone.utc),
            event_type="contract_rolled",
            payload={
                "instrument_id": str(instrument.id),
                "symbol": instrument.symbol,
                "old_security_id": old_security_id,
                "new_security_id": next_contract.security_id,
                "old_contract_expiry": old_expiry.isoformat() if old_expiry else None,
                "new_contract_expiry": next_contract.expiry.isoformat(),
            },
        )
    )
    logger.warning(
        "%s automatically rolled to next contract: security_id %s -> %s, expiry %s -> %s",
        instrument.symbol,
        old_security_id,
        next_contract.security_id,
        old_expiry,
        next_contract.expiry,
    )
    return True


__all__ = [
    "DELIVERY_CATEGORY",
    "close_out_cutoff_date",
    "is_past_close_out_cutoff",
    "roll_to_next_contract",
]
