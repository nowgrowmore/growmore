"""Fetch MCX option bhavcopy data into the local store, day by day.

    python -m research.mcx_options.fetch [--from 2026-01-01] [--to ...]
    python -m research.mcx_options.fetch --consolidate

**THIS PHASE DOES NOT SCRAPE THE LIVE SITE, AND `pytest` MUST NEVER MAKE A
REAL NETWORK CALL THROUGH THIS MODULE.** Unlike NSE (`research/stock_options/fetch.py`),
which downloads a fixed-URL zip per day, MCX has no static-URL daily bhavcopy
archive. Its public bhavcopy page (https://www.mcxindia.com/market-data/bhavcopy,
as of the research behind this module) is an ASP.NET Web Forms UI: getting a
day's CSV out of it means an initial GET to collect `__VIEWSTATE` /
`__EVENTVALIDATION` hidden fields, then a POST that replays them alongside the
requested date and any postback target -- classic ASP.NET scrape mechanics,
not a documented API.

`_scrape_live_site` below sketches that flow so the shape exists for later,
but it is UNVERIFIED -- NEEDS TESTING AGAINST THE LIVE SITE -- and this phase
deliberately does not wire it up as the default fetcher or call it from any
test. Everything in this module is designed around one seam instead: a
caller-supplied `fetcher(day) -> Optional[str]` that returns raw bhavcopy CSV
text (or None if MCX has nothing for that day), so the day-by-day loop,
caching, and parsing can all be exercised with a canned fake in unit tests.

Resumable like the NSE fetcher: a day already cached (even an empty one, for
a day MCX genuinely had nothing) is skipped on a later run.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from typing import Callable, Optional, Sequence

from research.mcx_options import chain_cache
from research.mcx_options.bhavcopy import parse_bhavcopy

#: A fetcher takes the trading day and returns raw bhavcopy CSV text, or None
#: if MCX has nothing for that day (holiday, or not yet published).
Fetcher = Callable[[date], Optional[str]]

#: UNVERIFIED - needs testing against the live site. Reconstructed from
#: public documentation of MCX's bhavcopy page structure, not confirmed by
#: actually driving it. Do not point real traffic at this without checking
#: the current page's field names first -- ASP.NET Web Forms postback field
#: names and the report's own query-string parameters are exactly the kind
#: of thing that silently changes between site revisions.
MCX_BHAVCOPY_URL = "https://www.mcxindia.com/market-data/bhavcopy"


def _scrape_live_site(day: date) -> Optional[str]:
    """Best-effort real fetcher -- UNVERIFIED, NEEDS TESTING AGAINST THE LIVE
    SITE. Not used as the default fetcher and not exercised by any test in
    this repo; `fetch_day`/`fetch_and_cache_day` always take an explicit
    `fetcher` in tests instead.

    The general shape a scrape-only ASP.NET report page needs:
      1. GET the report page to pick up `__VIEWSTATE`, `__VIEWSTATEGENERATOR`,
         and `__EVENTVALIDATION` hidden-field values, which the server
         validates on the following POST.
      2. POST back to the same URL with those fields replayed verbatim,
         plus the requested date and whatever the page's actual form-field
         names for "date" and "submit" turn out to be (placeholders below).
      3. The response is either the CSV directly, or another HTML page with
         a download link/button that needs a second request -- unconfirmed
         which, without having driven the real page.
    """
    import requests  # local import: never needed unless this path is taken

    session = requests.Session()
    get_resp = session.get(MCX_BHAVCOPY_URL, timeout=30)
    get_resp.raise_for_status()
    # TODO_VERIFY: real field names/regex for extracting these from the HTML.
    viewstate = _extract_hidden_field(get_resp.text, "__VIEWSTATE")
    event_validation = _extract_hidden_field(get_resp.text, "__EVENTVALIDATION")

    post_resp = session.post(
        MCX_BHAVCOPY_URL,
        data={
            "__VIEWSTATE": viewstate,
            "__EVENTVALIDATION": event_validation,
            # TODO_VERIFY: the real form field name(s) for the requested
            # date and the submit/download trigger.
            "ctl00$ContentPlaceHolder1$txtDate": day.strftime("%d/%m/%Y"),
            "ctl00$ContentPlaceHolder1$btnSubmit": "Submit",
        },
        timeout=30,
    )
    if post_resp.status_code != 200 or not post_resp.text:
        return None
    return post_resp.text


def _extract_hidden_field(html: str, field_name: str) -> str:
    """UNVERIFIED - needs testing against the live site's real markup."""
    import re

    match = re.search(
        rf'id="{field_name}"[^>]*value="([^"]*)"', html
    )
    return match.group(1) if match else ""


def fetch_day(day: date, fetcher: Optional[Fetcher] = None) -> list:
    """One day's MCX option rows, via `fetcher` (default: the real,
    UNVERIFIED live-site scraper).

    Never call this in a test without passing an injected `fetcher` -- the
    default hits the network.
    """
    fetch_fn = fetcher if fetcher is not None else _scrape_live_site
    csv_text = fetch_fn(day)
    if csv_text is None:
        return []
    return parse_bhavcopy(csv_text, trade_date=day)


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
    args = parser.parse_args(argv)

    if args.consolidate:
        print(f"consolidating {len(chain_cache.cached_days())} days -> per-symbol ...",
              file=sys.stderr)
        counts = chain_cache.consolidate_by_symbol()
        print(f"wrote {len(counts)} symbol files, {sum(counts.values()):,} rows total",
              file=sys.stderr)
        return 0

    if not args.from_date:
        parser.error("--from is required unless --consolidate is given")

    from_date = date.fromisoformat(args.from_date)
    to_date = date.fromisoformat(args.to_date)

    print(
        "NOTE: the live-site fetcher in this module is UNVERIFIED -- see the "
        "module docstring. This will likely fail until it has been tested "
        "and fixed against the real MCX bhavcopy page.",
        file=sys.stderr,
    )

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
