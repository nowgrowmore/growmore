"""Builds one commodity's live `MCXCycleData` from Dhan's real-time option
chain, expiry list, and historical daily futures bars.

The ONLY module in `growmore_bot/mcx_options/` allowed to call `DhanClient`
methods -- live analog of `growmore_bot/wheel_basket/live_iv_rank.py`'s role
for wheel_basket. `mcx_options_engine.py`'s decision logic never calls Dhan
directly; it only ever sees the plain `MCXCycleData` this module produces,
which keeps the decision logic unit-testable without any Dhan mock plumbing
inside it.

**Unverified response shape, carried forward from dhan_client.py**:
`DhanClient.get_option_chain`/`get_expiry_list`'s response-shape parsing is
NOT yet verified against a real live Dhan call (see dhan_client.py's own
docstrings and docs/pending-actions.md) -- the same open risk
wheel_basket/live_iv_rank.py already lives with in production. This module
does not paper over that: a shape mismatch anywhere in Dhan's response
(a missing key, an unparseable expiry string) is left to raise a clear,
loud `KeyError`/`ValueError` rather than being caught and silently turned
into a wrong number.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from growmore_bot.broker.dhan_client import Bar, DhanClient, OptionChainSnapshot

#: Trailing window fed to the regime classifier's warm-up (ADX(14) +
#: Bollinger Bandwidth(20) + its 60-day percentile rank need
#: 20 + 60 - 1 = 79 bars minimum; comfortably over-fetched here the same way
#: wheel_basket/live_iv_rank.py over-fetches for its own indicator warm-up).
FUTURES_HISTORY_WARMUP_DAYS = 180


@dataclass(frozen=True)
class MCXCycleData:
    """One commodity's market data for today's cycle decision -- everything
    `mcx_options_engine.run_cycle` needs, already fetched by this module.
    """

    futures_bars: tuple[Bar, ...]
    futures_price: float
    option_chain: OptionChainSnapshot
    option_expiry: date
    #: Years to `option_expiry` from `today`, floored at 0.
    T_years: float
    lot_size: int


def _parse_expiry(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(
            f"Could not parse Dhan expiry_list entry {raw!r} as an ISO date -- "
            "this likely means Dhan's expiry_list response shape does not match "
            "what DhanClient.get_expiry_list expects (see dhan_client.py's "
            "unverified-response-shape caveat)."
        ) from exc


def fetch_cycle_data(dhan_client: DhanClient, instrument: Any, today: date) -> MCXCycleData:
    """Fetch this cycle's futures history, nearest upcoming option expiry,
    and that expiry's option chain for one commodity `instrument`.

    Raises `ValueError` loudly (never returns a fabricated/partial result)
    if Dhan returns no historical bars at all, or no expiry on/after
    `today` -- both real "can't decide today" outcomes the caller
    (`mcx_options_engine`) must not be handed silently as zeros.
    """
    from_date = today - timedelta(days=FUTURES_HISTORY_WARMUP_DAYS)
    bars = dhan_client.get_historical_ohlc(
        instrument, from_date=from_date.isoformat(), to_date=today.isoformat(), interval="day",
    )
    if not bars:
        raise ValueError(
            f"No futures history returned for {getattr(instrument, 'symbol', instrument)!r} "
            f"between {from_date.isoformat()} and {today.isoformat()}"
        )
    futures_price = bars[-1].close

    raw_expiries = dhan_client.get_expiry_list(instrument)
    parsed_expiries = [_parse_expiry(e) for e in raw_expiries]
    upcoming = sorted(e for e in parsed_expiries if e >= today)
    if not upcoming:
        raise ValueError(
            f"No upcoming option expiry (on or after {today.isoformat()}) returned by Dhan "
            f"for {getattr(instrument, 'symbol', instrument)!r}"
        )
    option_expiry = upcoming[0]

    chain = dhan_client.get_option_chain(instrument, expiry=option_expiry.isoformat())

    T_years = max((option_expiry - today).days, 0) / 365.25
    lot_size = int(getattr(instrument, "lot_size", 1))

    return MCXCycleData(
        futures_bars=tuple(bars),
        futures_price=futures_price,
        option_chain=chain,
        option_expiry=option_expiry,
        T_years=T_years,
        lot_size=lot_size,
    )


__all__ = ["MCXCycleData", "FUTURES_HISTORY_WARMUP_DAYS", "fetch_cycle_data"]
