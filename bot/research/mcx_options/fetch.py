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

**How the real fetch works -- CONFIRMED live, not guessed.** MCX's public
bhavcopy page (https://www.mcxindia.com/market-data/bhavcopy) sits behind
Akamai Bot Manager, which was confirmed (across two separate machines/
networks) to block plain `requests`/`curl` -- even a cold GET of
`robots.txt` -- purely on TLS/JA3 fingerprint, not IP or headers: a real
browser passes every time, `requests` never does, and a real *headless*
Chromium (via Playwright) was ALSO blocked, narrowing it further to
TLS-handshake-level detection rather than a JS/behavioral challenge.
`curl_cffi` (see `pyproject.toml`'s `research` extra) replays an actual
Chrome TLS handshake and gets a clean 200 where `requests` cannot --
confirmed live.

The endpoint itself (discovered from a HAR capture of the account owner's
own real browser session against mcxindia.com -- NOT from reverse-engineering
their client-side code, which this project deliberately did not do) is a
plain JSON GET, not the ASP.NET-viewstate form or the `mcxlib` PageMethod
this module originally guessed at:

    GET https://www.mcxindia.com/market-data/bhavcopy/GetDateWiseBhavCopy
        ?InstrumentName=ALL&fromDate=DD/MM/YYYY
    -> {"IsSuccess": true, "Message": "...", "Data": [ {...one dict per row...}, ... ]}

`InstrumentName` only accepts `"ALL"` on this endpoint (confirmed: `"OPTCOM"`
alone returns `IsSuccess: false`), so every instrument (futures and options,
every commodity) comes back in one ~5-15k-row response per day; this module
filters to options client-side the same way `parse_bhavcopy_json` already
does (via `OptionType` being `CE`/`PE`, not `"-"`). Confirmed real field
names are documented in `bhavcopy.py`'s `_FIELD_ALIASES` comment.

A companion endpoint, `GetCommoditywiseBhavCopy?InstrumentName=OPTCOM&
Symbol=...&Expiry=DDMMMYYYY&fromDate=&toDate=`, returns one contract's ENTIRE
settlement history in a single call (confirmed: 7758 rows for one GOLDM
expiry) -- a much more efficient way to backfill one known contract's full
life than looping day-by-day, but it requires already knowing which expiries
exist; not wired up here since this module's day-by-day design (matching
`chain_cache.py`'s day-file layout) naturally discovers every expiry that
was listed on each day as it goes.

A same-origin warm-up GET to the bhavcopy page happens before the data call,
on the same session -- picked up Akamai's cookies organically in testing
even though the TLS fingerprint was already what made the difference; kept
as cheap insurance since it's exactly the "warm up on a real page, then
reuse the session" pattern NSE-scraping libraries (`nsepython`,
`jugaad-data`) use against the identical Akamai setup on nseindia.com.

Use `--probe` to fetch and print ONE real day's raw JSON without touching
the cache.

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
#: records (a list of dicts, straight out of the JSON response's `Data`),
#: or None if MCX has nothing for that day.
Fetcher = Callable[[date], Optional[list]]

#: CONFIRMED live (see module docstring) -- `InstrumentName` only accepts
#: `"ALL"` on this endpoint; asking for `"OPTCOM"` directly returns
#: `IsSuccess: false`, so every instrument comes back and options are
#: filtered out client-side.
MCX_BHAVCOPY_URL = "https://www.mcxindia.com/market-data/bhavcopy/GetDateWiseBhavCopy"

#: The page to GET first, on the same session, before the data call -- see
#: module docstring. Also doubles as the `Referer` header value below.
MCX_WARMUP_URL = "https://www.mcxindia.com/market-data/bhavcopy"

_BROWSER_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
}

_HTML_GET_HEADERS = {
    **_BROWSER_HEADERS,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_API_GET_HEADERS = {
    **_BROWSER_HEADERS,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": MCX_WARMUP_URL,
}

#: `curl_cffi`'s Chrome-TLS-handshake impersonation profile -- confirmed
#: live against mcxindia.com; see module docstring for why plain `requests`
#: doesn't work here regardless of headers.
_IMPERSONATE = "chrome124"


class _RawFetchResult:
    """Everything `--probe` needs to show a REAL diagnostic instead of a
    swallowed "no data" message -- see `_fetch_live_json_raw`."""

    def __init__(
        self, status_code=None, body_text=None, exception=None, json_body=None,
        warmup_status_code=None,
    ):
        self.status_code = status_code
        self.body_text = body_text
        self.exception = exception
        self.json_body = json_body
        #: HTTP status of the warm-up GET (see `MCX_WARMUP_URL`), so a
        #: `--probe` failure can be pinned to "the warm-up itself was
        #: blocked" versus "the warm-up succeeded but the data GET still
        #: wasn't accepted."
        self.warmup_status_code = warmup_status_code


def _fetch_live_json_raw(day: date, instrument: str = "ALL") -> _RawFetchResult:
    """The actual HTTP call, with nothing swallowed -- returns the status
    code / raw body / exception exactly as they happened. `_fetch_live_json`
    (below) wraps this into the simple `Optional[list]` contract the rest of
    this module needs; `--probe` uses THIS instead, specifically so "the
    request failed" and "MCX genuinely has nothing for this day" never look
    identical to whoever's debugging a live run.

    Warms the session up with a GET to `MCX_WARMUP_URL` first (see module
    docstring) before the data call, on one `curl_cffi` session (the TLS
    impersonation is what actually gets past Akamai; the warm-up is cheap
    extra insurance). The warm-up's own failure doesn't short-circuit -- the
    data GET is still attempted with whatever cookies (if any) the session
    picked up.
    """
    from curl_cffi import requests as cffi_requests  # local import: only needed here

    session = cffi_requests.Session(impersonate=_IMPERSONATE)

    warmup_status_code = None
    try:
        warmup_resp = session.get(MCX_WARMUP_URL, headers=_HTML_GET_HEADERS, timeout=30)
        warmup_status_code = warmup_resp.status_code
    except cffi_requests.exceptions.RequestException:
        pass  # still attempt the data GET below with whatever cookies exist

    params = {"InstrumentName": instrument, "fromDate": day.strftime("%d/%m/%Y")}
    try:
        resp = session.get(MCX_BHAVCOPY_URL, params=params, headers=_API_GET_HEADERS, timeout=30)
    except cffi_requests.exceptions.RequestException as exc:
        return _RawFetchResult(exception=exc, warmup_status_code=warmup_status_code)
    if not resp.ok:
        return _RawFetchResult(
            status_code=resp.status_code, body_text=resp.text,
            warmup_status_code=warmup_status_code,
        )
    try:
        body = resp.json()
    except ValueError as exc:
        return _RawFetchResult(
            status_code=resp.status_code, body_text=resp.text, exception=exc,
            warmup_status_code=warmup_status_code,
        )
    return _RawFetchResult(
        status_code=resp.status_code, json_body=body,
        warmup_status_code=warmup_status_code,
    )


def _fetch_live_json(day: date, instrument: str = "ALL") -> Optional[list]:
    """Real fetcher -- GETs `MCX_BHAVCOPY_URL`, see module docstring for the
    confirmed-live request/response shape. Not used by any test in this
    repo; `fetch_day`/`fetch_and_cache_day` always take an explicit
    `fetcher` in tests instead.

    Returns None (treated as "nothing for this day", e.g. a holiday) on any
    HTTP failure, a non-`IsSuccess` response, or an empty/missing `Data`
    list -- never raises for that case. Does raise if the response's *rows*
    don't match the expected field names (via `parse_bhavcopy_json`, called
    by `fetch_day`), since that's a real problem worth surfacing
    immediately, not a benign holiday.

    This collapses a real failure (blocked, timed out, wrong shape) and a
    genuine "MCX has nothing today" into the same `None` -- by design, for
    the backfill loop, where a resumable day-by-day run needs to treat both
    the same way (cache an empty day, move on). Use `--probe` (which calls
    `_fetch_live_json_raw` directly) to tell those two cases apart.
    """
    result = _fetch_live_json_raw(day, instrument)
    if result.json_body is None:
        return None
    body = result.json_body or {}
    if not body.get("IsSuccess"):
        return None
    rows = body.get("Data")
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
        result = _fetch_live_json_raw(day)
        print(f"(warm-up GET to {MCX_WARMUP_URL}: HTTP {result.warmup_status_code})",
              file=sys.stderr)
        if result.exception is not None:
            print(f"REQUEST FAILED: {type(result.exception).__name__}: {result.exception}",
                  file=sys.stderr)
            if result.status_code is not None:
                print(f"(HTTP {result.status_code} received before the failure; "
                      f"body: {(result.body_text or '')[:500]!r})", file=sys.stderr)
            return 1
        if result.status_code is not None and not (200 <= result.status_code < 300):
            print(f"HTTP {result.status_code} -- NOT a benign holiday, the request "
                  "itself was rejected. Body (first 500 chars):", file=sys.stderr)
            print((result.body_text or "")[:500], file=sys.stderr)
            return 1
        body = result.json_body or {}
        if not body.get("IsSuccess"):
            print(f"HTTP {result.status_code}, IsSuccess={body.get('IsSuccess')!r}, "
                  f"Message={body.get('Message')!r} -- no rows for {day} (likely a "
                  "genuine holiday/not-yet-published day, try a recent weekday).",
                  file=sys.stderr)
            return 1
        records = body.get("Data")
        if not records:
            print(f"HTTP {result.status_code}, IsSuccess=True, but no rows for {day} "
                  "-- inspect the full response below.", file=sys.stderr)
            print(json.dumps(body, indent=2)[:2000], file=sys.stderr)
            return 1
        options_only = [r for r in records if r.get("OptionType") in ("CE", "PE")]
        print(json.dumps(options_only[:3] or records[:3], indent=2))
        print(f"\n... ({len(records)} rows total, {len(options_only)} are options; "
              "showing first 3). If bhavcopy.py's parse_bhavcopy_json raises on this "
              "data, the error message names the exact keys seen above -- update "
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
