"""Fetch MCX option bhavcopy data into the local store, day by day.

    python -m research.mcx_options.fetch [--from 2026-01-01] [--to ...]
    python -m research.mcx_options.fetch --consolidate
    python -m research.mcx_options.fetch --probe --date 2026-09-04

**`pytest` MUST NEVER MAKE A REAL NETWORK CALL THROUGH THIS MODULE.**
Everything in this module is designed around one seam: a caller-supplied
`fetcher(day) -> Optional[list[dict]]` that returns one day's raw MCX
bhavcopy records (or None if MCX has nothing for that day), so the
day-by-day loop, caching, and parsing can all be exercised with a canned
fake in unit tests. `fetch_day`/`fetch_and_cache_day` only reach the network
through `_fetch_live_json` below, and only when no `fetcher` is injected.

**How the real fetch works.** MCX's public bhavcopy page
(https://www.mcxindia.com/market-data/bhavcopy) is NOT a static-URL archive
like NSE's -- but it is also not the scrape-only ASP.NET-viewstate form this
module originally assumed. Inspecting the third-party `mcxlib` PyPI
package's source (since the site itself returns HTTP 403 -- an Akamai edge
block -- to every request attempted *from this development environment*,
confirmed via plain `curl`, a cookie-establishing session, and a real
headless-Chromium Playwright browser, all blocked identically, while the
account owner confirmed the exact same URL loads fine in their own Brave
browser) shows the real mechanism is a same-origin JSON POST against an
ASP.NET PageMethod:

    POST https://www.mcxindia.com/backpage.aspx/GetDateWiseBhavCopy
    Content-Type: application/json
    X-Requested-With: XMLHttpRequest
    body: {"Date": "YYYYMMDD", "InstrumentName": "OPTFUT"}
    -> {"d": {"Data": [ {...one dict per row...}, ... ]}}

`_fetch_live_json` below implements exactly this (verified against
`mcxlib`'s published source, not against a live response -- see next
paragraph). This is a considerably stronger starting point than the
original viewstate-replay guess: there's no `__VIEWSTATE`/postback dance to
get right, just one POST. What's still unverified is the *shape of each row
dict* in the response (field names) -- see `bhavcopy.py`'s
`_FIELD_ALIASES` and `parse_bhavcopy_json`'s loud-failure-on-first-row
diagnostic for how that gets caught and fixed quickly once someone with a
working network path (i.e. NOT this environment -- run this from the
account owner's own machine/network, where mcxindia.com is reachable) sees
a real response.

Use `--probe` to fetch and print ONE real day's raw JSON without touching
the cache, specifically to confirm/fix the field-name mapping on a machine
that can actually reach MCX.

Resumable like the NSE fetcher: a day already cached (even an empty one, for
a day MCX genuinely had nothing) is skipped on a later run.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from typing import Callable, Optional, Sequence

from research.mcx_options import chain_cache
from research.mcx_options.bhavcopy import parse_bhavcopy_json

#: A fetcher takes the trading day and returns one day's raw MCX bhavcopy
#: records (a list of dicts, straight out of the JSON response's
#: `d.Data`), or None if MCX has nothing for that day.
Fetcher = Callable[[date], Optional[list]]

#: Verified against `mcxlib`'s published source (see module docstring) --
#: NOT verified against a live response, since this environment cannot
#: reach mcxindia.com (Akamai edge block, confirmed site-wide: even
#: `robots.txt` 403s here, while the same URLs load fine in the account
#: owner's own browser -- this looks like a block on this environment's
#: specific outbound network path, not a bot-detection or IP-reputation
#: issue that better headers/a real browser can route around; both were
#: tried and both still 403).
MCX_BHAVCOPY_URL = "https://www.mcxindia.com/backpage.aspx/GetDateWiseBhavCopy"

_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://www.mcxindia.com",
    "Referer": "https://www.mcxindia.com/market-data/bhavcopy",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
}


def _fetch_live_json(day: date, instrument: str = "OPTFUT") -> Optional[list]:
    """Real fetcher -- POSTs to `MCX_BHAVCOPY_URL`, see module docstring for
    exactly what's verified (the endpoint/request shape, via `mcxlib`) versus
    still unverified (the response row schema, since no real response has
    been seen from this environment). Not used by any test in this repo;
    `fetch_day`/`fetch_and_cache_day` always take an explicit `fetcher` in
    tests instead.

    Returns None (treated as "nothing for this day", e.g. a holiday) on any
    HTTP failure or an empty/missing `Data` list -- never raises for that
    case. Does raise if the response's *rows* don't match the expected
    field names (via `parse_bhavcopy_json`, called by `fetch_day`), since
    that's a real problem worth surfacing immediately, not a benign holiday.
    """
    import requests  # local import: never needed unless this path is taken

    session = requests.Session()
    session.trust_env = False
    payload = json.dumps({"Date": day.strftime("%Y%m%d"), "InstrumentName": instrument})
    try:
        resp = session.post(MCX_BHAVCOPY_URL, headers=_HEADERS, data=payload, timeout=30)
    except requests.RequestException:
        return None
    if not resp.ok:
        return None
    try:
        body = resp.json()
    except ValueError:
        return None
    rows = (body or {}).get("d", {}).get("Data")
    return rows or None


def fetch_day(day: date, fetcher: Optional[Fetcher] = None) -> list:
    """One day's MCX option rows, via `fetcher` (default: the real fetcher,
    `_fetch_live_json` -- see module docstring on what's verified there).

    Never call this in a test without passing an injected `fetcher` -- the
    default hits the network.
    """
    fetch_fn = fetcher if fetcher is not None else _fetch_live_json
    records = fetch_fn(day)
    if not records:
        return []
    return parse_bhavcopy_json(records, trade_date=day)


def fetch_and_cache_day(day: date, fetcher: Optional[Fetcher] = None) -> int:
    """Fetch one day and write it into `chain_cache`, returning the row count.

    Always writes a day file, even an empty one, so a day MCX genuinely has
    nothing for (holiday, or not yet published) is recorded as done and not
    re-fetched on a later resumed run.
    """
    rows = fetch_day(day, fetcher=fetcher)
    chain_cache.save_day(day, chain_cache.rows_to_frame(rows))
    return len(rows)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="from_date", required=False)
    parser.add_argument("--to", dest="to_date", default=date.today().isoformat())
    parser.add_argument(
        "--consolidate", action="store_true",
        help="Rewrite cached days as one parquet per underlying, then exit.",
    )
    parser.add_argument(
        "--probe", action="store_true",
        help=(
            "Fetch ONE real day (see --date) and print the raw JSON response "
            "verbatim, without touching the cache or parsing it. Run this "
            "from a machine that can actually reach mcxindia.com to confirm "
            "(and if needed, fix) the field-name guesses in "
            "research/mcx_options/bhavcopy.py's _FIELD_ALIASES."
        ),
    )
    parser.add_argument(
        "--date", dest="probe_date", default=date.today().isoformat(),
        help="Day to fetch for --probe (YYYY-MM-DD, default: today).",
    )
    args = parser.parse_args(argv)

    if args.probe:
        day = date.fromisoformat(args.probe_date)
        records = _fetch_live_json(day)
        if not records:
            print(f"no data returned for {day} (holiday, not yet published, "
                  "or the request failed -- rerun with a recent weekday)",
                  file=sys.stderr)
            return 1
        print(json.dumps(records[:3], indent=2))
        print(f"\n... ({len(records)} rows total; showing first 3). "
              "If bhavcopy.py's parse_bhavcopy_json raises on this data, "
              "the error message names the exact keys seen above -- update "
              "_FIELD_ALIASES to match.", file=sys.stderr)
        return 0

    if args.consolidate:
        print(f"consolidating {len(chain_cache.cached_days())} days -> per-symbol ...",
              file=sys.stderr)
        counts = chain_cache.consolidate_by_symbol()
        print(f"wrote {len(counts)} symbol files, {sum(counts.values()):,} rows total",
              file=sys.stderr)
        return 0

    if not args.from_date:
        parser.error("--from is required unless --consolidate or --probe is given")

    from_date = date.fromisoformat(args.from_date)
    to_date = date.fromisoformat(args.to_date)

    day = from_date
    fetched = skipped = 0
    while day <= to_date:
        if chain_cache.is_day_cached(day):
            skipped += 1
        else:
            count = fetch_and_cache_day(day)
            fetched += 1
            print(f"  {day}: {count} rows", file=sys.stderr)
        day += timedelta(days=1)

    print(f"\nfetched {fetched}, skipped {skipped} already cached", file=sys.stderr)
    print("now run: python -m research.mcx_options.fetch --consolidate", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "Fetcher", "MCX_BHAVCOPY_URL", "fetch_day", "fetch_and_cache_day", "main",
]
