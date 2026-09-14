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


#: *** ALSO UNVERIFIED -- see module docstring. *** MCX's actual public
#: bhavcopy delivery mechanism (discovered from the third-party `mcxlib`
#: package's source, since MCX's own site is unreachable for direct
#: inspection from this environment -- see `fetch.py`'s docstring) is not a
#: CSV file at all: it's a JSON POST to `backpage.aspx/GetDateWiseBhavCopy`,
#: an ASP.NET PageMethod, returning `{"d": {"Data": [ {...one dict per
#: row...} ]}}`. The *field names inside each row dict* are still unknown --
#: `mcxlib` just hands the raw dicts to `pandas.DataFrame.from_dict` without
#: documenting them. The alias lists below are best-guess candidates (MCX's
#: ASP.NET backend conventionally uses PascalCase); `parse_bhavcopy_json`
#: tries each in turn per field and raises a `ValueError` naming the exact
#: keys it actually saw on the first row it can't map, rather than silently
#: returning nothing -- so a real response (fetched from a machine that can
#: actually reach mcxindia.com, see `fetch.py`) turns into a one-line fix
#: here instead of a silent wrong parse.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("Symbol", "Commodity", "CommodityName", "SYMBOL"),
    "expiry": ("ExpiryDate", "Expiry", "EXPIRY"),
    "strike": ("StrikePrice", "Strike", "STRIKE"),
    "opt_type": ("OptionType", "OptType", "OPTIONTYPE"),
    "open": ("Open", "OpenPrice", "OPEN"),
    "high": ("High", "HighPrice", "HIGH"),
    "low": ("Low", "LowPrice", "LOW"),
    "close": ("Close", "ClosePrice", "CLOSE"),
    "previous_close": ("PreviousClose", "PrevClose", "PREVIOUS_CLOSE"),
    "open_interest": ("OpenInterest", "OI", "OPEN_INT"),
    "volume": ("Volume", "VOLUME"),
    "value": ("Value", "ValueInLakhs", "VALUE"),
}

_MCX_JSON_DATE_PATTERN = __import__("re").compile(r"/Date\((-?\d+)(?:[+-]\d+)?\)/")


def _pick(record: dict, field: str) -> object:
    for key in _FIELD_ALIASES[field]:
        if key in record:
            return record[key]
    raise KeyError(
        f"none of {_FIELD_ALIASES[field]!r} found for {field!r} in a real MCX "
        f"response row -- actual keys seen: {sorted(record.keys())!r}. Update "
        f"_FIELD_ALIASES[{field!r}] in research/mcx_options/bhavcopy.py to "
        f"include the real key name."
    )


def _json_date(value: object) -> date:
    """MCX's JSON endpoints commonly encode dates as ASP.NET's
    `/Date(epoch_ms)/` wrapper (confirmed elsewhere on the site via
    `mcxlib`'s `_MCX_DATE_PATTERN`), but this hasn't been confirmed for the
    bhavcopy endpoint specifically -- also accepts a handful of plain date
    strings as a fallback in case it turns out to be a normal string field.
    """
    text = str(value).strip()
    match = _MCX_JSON_DATE_PATTERN.fullmatch(text)
    if match:
        from datetime import timezone

        millis = int(match.group(1))
        return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).date()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text.upper(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised MCX JSON date value: {value!r}")


def parse_bhavcopy_json(records: list[dict], trade_date: date) -> list[MCXOptionRow]:
    """MCX option rows from one day's bhavcopy, fetched as JSON via
    `fetch.py`'s `_fetch_live_json` (the `backpage.aspx/GetDateWiseBhavCopy`
    endpoint) rather than a CSV file -- see the module-level note above on
    why this exists alongside `parse_bhavcopy` and why its field names are
    still unverified.

    Keeps only records whose resolved option-type is CE/PE. A record this
    function cannot map AT ALL (missing every alias for a required field) is
    skipped defensively, same discipline as `parse_bhavcopy` -- except the
    FIRST such failure is re-raised with the diagnostic from `_pick` so a
    real, live-fetched response that doesn't match `_FIELD_ALIASES` fails
    loudly and immediately instead of silently returning zero rows.
    """
    rows: list[MCXOptionRow] = []
    for i, raw in enumerate(records):
        try:
            opt_type = str(_pick(raw, "opt_type")).strip().upper()
        except KeyError:
            if i == 0:
                raise
            continue
        if opt_type not in _OPTION_TYPES:
            continue
        try:
            rows.append(
                MCXOptionRow(
                    trade_date=trade_date,
                    symbol=str(_pick(raw, "symbol")).strip(),
                    expiry=_json_date(_pick(raw, "expiry")),
                    strike=float(_pick(raw, "strike") or 0),
                    opt_type=opt_type,
                    open=float(_pick(raw, "open") or 0),
                    high=float(_pick(raw, "high") or 0),
                    low=float(_pick(raw, "low") or 0),
                    close=float(_pick(raw, "close") or 0),
                    previous_close=float(_pick(raw, "previous_close") or 0),
                    open_interest=int(float(_pick(raw, "open_interest") or 0)),
                    volume=int(float(_pick(raw, "volume") or 0)),
                    value=float(_pick(raw, "value") or 0),
                )
            )
        except KeyError:
            if i == 0:
                raise
            continue  # one bad row must not cost the day
        except (ValueError, TypeError):
            continue
    return rows


__all__ = ["MCXOptionRow", "parse_bhavcopy", "parse_bhavcopy_json"]
