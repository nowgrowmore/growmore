"""Parse MCX's options bhavcopy CSV rows into one row shape.

*** THE COLUMN NAMES BELOW ARE UNVERIFIED AGAINST A REAL DOWNLOADED MCX
BHAVCOPY FILE. *** Unlike NSE (fixed-URL public archive, see
`research/stock_options/bhavcopy.py`), MCX publishes its bhavcopy only
through a scrape-only ASP.NET UI (see `fetch.py`'s docstring), so there was
no real file on hand to inspect while writing this parser. The schema
assumed here --

    SYMBOL, EXPIRY, STRIKE, OPTIONTYPE, OPEN, HIGH, LOW, CLOSE,
    PREVIOUS_CLOSE, VOLUME, VALUE, OPEN_INT

-- is reconstructed from prior public-documentation research into MCX
bhavcopy fields, NOT confirmed against an actual downloaded file.
**CONFIRM every column name here against a real MCX bhavcopy before
trusting anything this parser produces from live-fetched data.**

Two more assumptions baked into this module, flagged the same way:

  * **No trade-date column.** The schema above has no per-row date field
    (MCX's bhavcopy, like most exchange bhavcopies, is one file per trading
    day). `parse_bhavcopy` therefore takes `trade_date` as a parameter,
    stamped onto every row, rather than reading it from a column. If a real
    file turns out to carry its own date column, this should switch to
    reading it (and cross-checking it against the caller's `trade_date`)
    rather than trusting the caller blindly.
  * **No distinct settlement-price column.** Unlike NSE's UDiFF file (which
    carries `SttlmPric` separately from `ClsPric`), nothing in the
    documentation reviewed here confirms MCX exposes a settlement price
    distinct from `CLOSE`. `close` is used as the de facto settlement price
    downstream for now; TODO_VERIFY whether MCX options actually settle off
    a different, unpublished figure.

Mirrors the defensive-parsing conventions of `research/stock_options/bhavcopy.py`:
a malformed row is skipped rather than raised on (one bad line must not cost
the whole day's chain), and `is_tradeable` gates strike selection on real
traded volume -- MCX, like NSE, is expected to list every strike it makes
available, including ones nobody actually dealt in that day.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

_OPTION_TYPES = ("CE", "PE")


@dataclass(frozen=True)
class MCXOptionRow:
    """One (date, symbol, expiry, strike, type) MCX option settlement record."""

    trade_date: date
    symbol: str
    expiry: date
    strike: float
    opt_type: str  # "CE" | "PE"
    open: float
    high: float
    low: float
    close: float
    previous_close: float
    open_interest: int
    volume: int
    #: Traded value in rupees, as reported by the exchange. Units/precision
    #: unconfirmed -- see module docstring.
    value: float

    @property
    def is_tradeable(self) -> bool:
        """Whether this strike actually printed today.

        Bhavcopy is expected to list every strike MCX offers, including ones
        that never traded. Selling those in a backtest is fiction, so the
        chain is kept complete (strike selection needs to see it) while the
        engine is only allowed to transact where volume was real.
        """
        return self.volume > 0


def _f(value: Optional[str]) -> float:
    return float((value or "0").strip() or 0)


def _i(value: Optional[str]) -> int:
    return int(float((value or "0").strip() or 0))


def _mcx_date(value: str) -> date:
    """MCX bhavcopy dates are assumed `DD-MON-YYYY` (e.g. `26-SEP-2026`) --
    UNVERIFIED, mirrored from NSE's legacy bhavcopy convention for lack of a
    real file to check against. See module docstring."""
    return datetime.strptime(value.strip().upper(), "%d-%b-%Y").date()


def parse_bhavcopy(csv_text: str, trade_date: date) -> list[MCXOptionRow]:
    """MCX option rows from one day's bhavcopy CSV text.

    Keeps only rows whose OPTIONTYPE is CE/PE -- expected to exclude futures
    rows in the same file, though that exclusion rule is itself unverified
    against a real file (see module docstring). A malformed row is skipped
    rather than raised on.
    """
    rows: list[MCXOptionRow] = []
    for raw in csv.DictReader(io.StringIO(csv_text)):
        opt_type = (raw.get("OPTIONTYPE") or "").strip()
        if opt_type not in _OPTION_TYPES:
            continue
        try:
            rows.append(
                MCXOptionRow(
                    trade_date=trade_date,
                    symbol=raw["SYMBOL"].strip(),
                    expiry=_mcx_date(raw["EXPIRY"]),
                    strike=_f(raw.get("STRIKE")),
                    opt_type=opt_type,
                    open=_f(raw.get("OPEN")),
                    high=_f(raw.get("HIGH")),
                    low=_f(raw.get("LOW")),
                    close=_f(raw.get("CLOSE")),
                    previous_close=_f(raw.get("PREVIOUS_CLOSE")),
                    open_interest=_i(raw.get("OPEN_INT")),
                    volume=_i(raw.get("VOLUME")),
                    value=_f(raw.get("VALUE")),
                )
            )
        except (KeyError, ValueError, AttributeError):
            continue  # one bad line must not cost the day
    return rows


__all__ = ["MCXOptionRow", "parse_bhavcopy"]
