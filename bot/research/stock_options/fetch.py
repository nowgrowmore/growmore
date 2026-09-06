"""Fetch NSE stock-option chains into the local store.

    python -m research.stock_options.fetch [--from 2019-09-01] [--to ...]
    python -m research.stock_options.fetch --consolidate

**No token, no Dhan, no database.** NSE publishes the F&O bhavcopy publicly;
Dhan cannot serve this data at all (it has no history for expired contracts),
which is why this study goes to the exchange directly.

Two formats, chosen by trying the modern one first and falling back:

    UDiFF   .../content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip
    legacy  .../content/historical/DERIVATIVES/YYYY/MON/foDDMONYYYYbhav.csv.zip

Resumable: a day already cached is skipped, so a crash or a network blip
900 days in costs nothing.

TRADING DAYS come from the cached cash-equity series rather than a holiday
calendar -- if RELIANCE printed a bar, the market was open. That reuses data
already on disk and cannot drift out of date the way a hardcoded calendar
would.

TWO FILTERS keep 46M rows manageable and honest:
  * only the 210 symbols in the committed F&O manifest;
  * only strikes within +-25% of the underlying, which is far wider than any
    strategy here reaches and keeps the store at ~90 MB compressed.

ADJUSTED VS UNADJUSTED IS THE TRAP HERE, and it is silent. The cached cash
series (`research/.cache/fno_bars/`) is corporate-action ADJUSTED, but option
strikes in a historical bhavcopy are the strikes that actually traded, i.e.
UNADJUSTED. RELIANCE on 2019-10-03 closes at 589.54 adjusted while its puts
sat between 900 and 1600 -- a 2.14x mismatch from the October 2024 bonus.
Filtering one against the other silently discards every row, which is exactly
what it did on the first run.

So the underlying comes from an UNADJUSTED source in both eras: `UndrlygPric`
for UDiFF, and NSE's CASH bhavcopy (one extra ~70 KB download) for legacy
days. Everything downstream -- strike selection, settlement, physical
delivery -- then lives consistently in unadjusted prices, which is the space
the contracts themselves are denominated in.
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
import time
import zipfile
from datetime import date
from typing import Optional, Sequence

import requests

from research.fno import bar_cache as cash_bars
from research.fno.manifest import load_manifest
from research.stock_options import chain_cache
from research.stock_options.bhavcopy import parse_legacy, parse_udiff

UDIFF_URL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d}_F_0000.csv.zip"
LEGACY_URL = (
    "https://nsearchives.nseindia.com/content/historical/DERIVATIVES/"
    "{yyyy}/{MON}/fo{dd}{MON}{yyyy}bhav.csv.zip"
)
#: The CASH bhavcopy, needed only for legacy days -- see `unadjusted_closes`.
LEGACY_CASH_URL = (
    "https://nsearchives.nseindia.com/content/historical/EQUITIES/"
    "{yyyy}/{MON}/cm{dd}{MON}{yyyy}bhav.csv.zip"
)
#: NSE's archive host blocks default HTTP clients -- same header the
#: small-cap universe fetcher already needs.
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

REQUEST_DELAY_SECONDS = 0.6
MAX_RETRIES = 3
MONEYNESS_LIMIT = 0.25

#: Physical settlement for stock derivatives became fully effective with the
#: October 2019 expiry. Before that they were cash-settled, and the wheel is
#: simply not the same strategy, so the study starts here.
DEFAULT_FROM = date(2019, 9, 1)


def _download(url: str) -> Optional[str]:
    """GET and unzip, retrying transient network failures.

    The retry lives here rather than at the call site because there are two
    downloads per legacy day -- the F&O file and the cash file -- and an
    unretried SSL blip on the second one killed a 1,740-day run at day 20.
    A 404 is not transient and returns None immediately.
    """
    response = None
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=60)
            break
        except requests.RequestException:
            if attempt == MAX_RETRIES - 1:
                return None
            time.sleep(REQUEST_DELAY_SECONDS * (2 ** attempt) * 3)
    if response is None or response.status_code != 200 or not response.content:
        return None
    try:
        archive = zipfile.ZipFile(io.BytesIO(response.content))
    except zipfile.BadZipFile:
        return None
    name = next((n for n in archive.namelist() if n.lower().endswith(".csv")), None)
    return archive.read(name).decode("utf-8", errors="replace") if name else None


def fetch_day(day: date) -> Optional[tuple[str, str]]:
    """(csv_text, format) for one trading day, or None if NSE has nothing."""
    text = _download(UDIFF_URL.format(d=day.strftime("%Y%m%d")))
    if text is not None:
        return text, "udiff"
    text = _download(
        LEGACY_URL.format(yyyy=day.year, MON=day.strftime("%b").upper(), dd=day.strftime("%d"))
    )
    return (text, "legacy") if text is not None else None


def trading_days(from_date: date, to_date: date, reference: str = "RELIANCE") -> list[date]:
    """Days the cash market actually printed, from the cached equity series."""
    bars = cash_bars.load(reference, from_date=from_date, to_date=to_date)
    return [cash_bars.trading_date(b.timestamp) for b in bars]


def unadjusted_closes(day: date) -> dict[str, float]:
    """symbol -> UNADJUSTED close from NSE's cash bhavcopy for `day`.

    Only needed for legacy F&O days; UDiFF carries `UndrlygPric` itself.
    Returns {} when NSE has no file, which the caller treats as "cannot price
    this day" rather than as zero.
    """
    text = _download(
        LEGACY_CASH_URL.format(
            yyyy=day.year, MON=day.strftime("%b").upper(), dd=day.strftime("%d")
        )
    )
    if text is None:
        return {}
    closes: dict[str, float] = {}
    for raw in csv.DictReader(io.StringIO(text)):
        if (raw.get("SERIES") or "").strip() != "EQ":
            continue
        try:
            closes[raw["SYMBOL"].strip()] = float(raw["CLOSE"])
        except (KeyError, ValueError):
            continue
    return closes


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="from_date", default=DEFAULT_FROM.isoformat())
    parser.add_argument("--to", dest="to_date", default=date.today().isoformat())
    parser.add_argument("--consolidate", action="store_true",
                        help="Rewrite cached days as one parquet per underlying, then exit.")
    args = parser.parse_args(argv)

    rows = load_manifest()
    symbols = {r.symbol for r in rows}

    if args.consolidate:
        print(f"consolidating {len(chain_cache.cached_days())} days -> per-symbol ...",
              file=sys.stderr)
        counts = chain_cache.consolidate_by_symbol(symbols)
        print(f"wrote {len(counts)} symbol files, {sum(counts.values()):,} rows total",
              file=sys.stderr)
        return 0

    from_date = date.fromisoformat(args.from_date)
    to_date = date.fromisoformat(args.to_date)
    days = trading_days(from_date, to_date)
    print(f"{len(days)} trading days {from_date} -> {to_date}", file=sys.stderr)

    fetched = skipped = missing = 0
    for index, day in enumerate(days, start=1):
        if chain_cache.is_day_cached(day):
            skipped += 1
            continue

        payload = None
        for attempt in range(MAX_RETRIES):
            try:
                payload = fetch_day(day)
                break
            except Exception as exc:  # noqa: BLE001 -- retry anything transient
                wait = REQUEST_DELAY_SECONDS * (2 ** attempt) * 4
                print(f"  {day}: {str(exc)[:60]} -- retry in {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
        time.sleep(REQUEST_DELAY_SECONDS)

        if payload is None:
            # A market holiday the cash series disagrees about, or a gap in
            # NSE's archive. Recorded as an empty day so it is not re-fetched.
            chain_cache.save_day(day, chain_cache.rows_to_frame([]))
            missing += 1
            continue

        text, fmt = payload
        parsed = parse_udiff(text) if fmt == "udiff" else parse_legacy(text)
        # Legacy F&O files carry no underlying price, and the cached cash
        # series is ADJUSTED so it cannot supply one. Go to the cash bhavcopy.
        spot: dict[str, float] = {}
        if fmt == "legacy":
            spot = unadjusted_closes(day)
            time.sleep(REQUEST_DELAY_SECONDS)
        kept = []
        for row in parsed:
            if row.symbol not in symbols:
                continue
            underlying = row.underlying or spot.get(row.symbol)
            if not underlying or underlying <= 0:
                continue
            if abs(row.strike / underlying - 1.0) > MONEYNESS_LIMIT:
                continue
            kept.append(row if row.underlying else _with_underlying(row, underlying))

        chain_cache.save_day(day, chain_cache.rows_to_frame(kept))
        fetched += 1
        if index % 25 == 0 or fetched <= 3:
            print(f"  [{index:4d}/{len(days)}] {day} {fmt:<6} {len(kept):6,} rows",
                  file=sys.stderr)

    print(f"\nfetched {fetched}, skipped {skipped} already cached, {missing} with no file",
          file=sys.stderr)
    print("now run: python -m research.stock_options.fetch --consolidate", file=sys.stderr)
    return 0


def _with_underlying(row, underlying: float):
    from dataclasses import replace
    return replace(row, underlying=underlying)


if __name__ == "__main__":
    raise SystemExit(main())
