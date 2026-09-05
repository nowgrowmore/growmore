"""The NSE F&O underlying universe, derived from Dhan's public scrip master.

Confirmed against a live download 2026-09-05 (200,289 rows total, 122,492 NSE):

    Counter({('D','OPTSTK'): 64892, ('M','OPTFUT'): 23666, ('D','OPTIDX'): 11822,
             ('C','OPTCUR'): 11300, ('E','EQUITY'): 9899, ('D','FUTSTK'): 647,
             ('C','FUTCUR'): 129, ('I','INDEX'): 119, ('D','FUTIDX'): 18})

647 FUTSTK rows collapse to **228 unique underlyings** across three expiries.

Two things about that file drive the shape of this module:

  * `SM_SYMBOL_NAME` is EMPTY on every FUTSTK row -- unlike the MCX FUTCOM
    rows `growmore_bot.broker.instrument_master` matches on, where it carries
    "GOLDM". So the underlying has to be parsed out of `SEM_TRADING_SYMBOL`,
    which is `<UNDERLYING>-<Mon><YYYY>-FUT` (e.g. "RELIANCE-Sep2026-FUT").
    A parser that trusted SM_SYMBOL_NAME returns exactly one nameless
    underlying for the whole exchange.
  * 18 of the 228 are exchange test symbols, "011NSETEST" .. "181NSETEST".
    They have REAL `NSE`/`E`/`EQUITY` rows too, so mapping a symbol to a
    security_id does NOT exclude them. Membership therefore needs a third
    clause -- see `research.fno.manifest`.

Both maps are built in a SINGLE PASS over the CSV.
`research.smallcap_momentum.security_mapping.match_nse_equity_security_id`
already does the equity lookup, but it rescans all ~200k rows per symbol;
at 210 symbols that is 42M row-parses, so it is reused as the reference
implementation in the tests rather than in the hot path.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Optional

#: `<UNDERLYING>-<Mon><YYYY>-FUT`. The underlying is greedy up to the LAST
#: such suffix, so a symbol containing a hyphen survives intact.
_FUT_SUFFIX = re.compile(r"^(?P<underlying>.+)-[A-Z][a-z]{2}\d{4}-FUT$")


def parse_fno_underlyings(csv_text: str) -> set[str]:
    """Every distinct stock-futures underlying on NSE.

    FUTSTK only: FUTIDX (index futures) and OPTSTK (whose trading symbols
    carry a strike and an option type) are deliberately excluded, as are
    MCX FUTCOM rows that share the `-MonYYYY-FUT` shape.
    """
    underlyings: set[str] = set()
    for row in csv.DictReader(io.StringIO(csv_text)):
        if row.get("SEM_EXM_EXCH_ID") != "NSE":
            continue
        if row.get("SEM_INSTRUMENT_NAME") != "FUTSTK":
            continue
        match = _FUT_SUFFIX.match((row.get("SEM_TRADING_SYMBOL") or "").strip())
        if match:
            underlyings.add(match.group("underlying"))
    return underlyings


def nse_equity_security_ids(csv_text: str) -> dict[str, str]:
    """Trading symbol -> Dhan `NSE_EQ` security_id, cash segment only.

    A cash-equity row is `SEM_EXM_EXCH_ID == "NSE"` AND `SEM_SEGMENT == "E"`;
    both fields are required because the same symbol also appears on the F&O
    ("D"), index ("I") and MCX segments. First row wins -- duplicates within
    the cash segment have not been observed.
    """
    ids: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        if row.get("SEM_EXM_EXCH_ID") != "NSE":
            continue
        if row.get("SEM_SEGMENT") != "E":
            continue
        if row.get("SEM_INSTRUMENT_NAME") != "EQUITY":
            continue
        symbol = (row.get("SEM_TRADING_SYMBOL") or "").strip()
        security_id = (row.get("SEM_SMST_SECURITY_ID") or "").strip()
        if symbol and security_id:
            ids.setdefault(symbol, security_id)
    return ids


def fno_lot_sizes(csv_text: str) -> dict[str, int]:
    """Underlying -> F&O lot size, from the nearest-expiry FUTSTK row.

    Unlike the MCX FUTCOM rows -- where `SEM_LOT_UNITS` is 1.0 for every row
    and is documented as unusable -- NSE FUTSTK rows carry the real market
    lot (e.g. 500.0 for Reliance). Not used for cash-equity sizing (see
    `growmore_bot.risk.sizing.shares_for_capital`); recorded in the manifest
    because it is the only liquidity-tier signal the scrip master offers.
    """
    lots: dict[str, tuple[str, int]] = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        if row.get("SEM_EXM_EXCH_ID") != "NSE":
            continue
        if row.get("SEM_INSTRUMENT_NAME") != "FUTSTK":
            continue
        match = _FUT_SUFFIX.match((row.get("SEM_TRADING_SYMBOL") or "").strip())
        if not match:
            continue
        expiry = (row.get("SEM_EXPIRY_DATE") or "").strip()
        try:
            lot = int(float(row.get("SEM_LOT_UNITS") or 0))
        except ValueError:
            continue
        if lot <= 0:
            continue
        underlying = match.group("underlying")
        previous: Optional[tuple[str, int]] = lots.get(underlying)
        if previous is None or expiry < previous[0]:
            lots[underlying] = (expiry, lot)
    return {symbol: lot for symbol, (_expiry, lot) in lots.items()}


__all__ = ["parse_fno_underlyings", "nse_equity_security_ids", "fno_lot_sizes"]
