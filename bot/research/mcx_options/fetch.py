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

#: The page to GET first, to acquire Akamai Bot Manager's session cookies
#: (`_abck`, `bm_sz`, `ak_bmsc`) organically before the API POST -- the same
#: "warm up a session on a real HTML page, then reuse it" pattern the NSE-
#: scraping community (`nsepython`, `jugaad-data`) uses against the
#: identical Akamai setup on nseindia.com. A cold POST with no prior page
#: visit in the session was confirmed (both from this environment and the
#: account owner's own machine/network) to get a 403 -- this warm-up step is
#: the standard next thing to try before reaching for anything heavier
#: (e.g. TLS-fingerprint-impersonating clients like `curl_cffi`).
MCX_WARMUP_URL = "https://www.mcxindia.com/market-data/bhavcopy"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_HTML_GET_HEADERS = {
    **_BROWSER_HEADERS,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_API_POST_HEADERS = {
    **_BROWSER_HEADERS,
    "Content-Type": "application/json",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://www.mcxindia.com",
    "Referer": MCX_WARMUP_URL,
}

#: Back-compat alias -- some callers/tests referred to the POST headers by
#: this name before the warm-up GET was added.
_HEADERS = _API_POST_HEADERS


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
        #: blocked" versus "the warm-up succeeded but the API POST still
        #: wasn't accepted."
        self.warmup_status_code = warmup_status_code


def _fetch_live_json_raw(day: date, instrument: str = "OPTFUT") -> _RawFetchResult:
    """The actual HTTP call, with nothing swallowed -- returns the status
    code / raw body / exception exactly as they happened. `_fetch_live_json`
    (below) wraps this into the simple `Optional[list]` contract the rest of
    this module needs; `--probe` uses THIS instead, specifically so "the
    request failed" and "MCX genuinely has nothing for this day" never look
    identical to whoever's debugging a live run.

    Warms the session up with a GET to `MCX_WARMUP_URL` first (see its
    docstring) so Akamai's cookies are set organically before the API call,
    rather than POSTing cold. The warm-up's own failure doesn't short-circuit
    -- the POST is still attempted with whatever cookies (if any) the
    session picked up, since a non-2xx warm-up page load doesn't necessarily
    mean the cookies Akamai cares about weren't still set on the response.
    """
    import requests  # local import: never needed unless this path is taken

    session = requests.Session()
    session.trust_env = False

    warmup_status_code = None
    try:
        warmup_resp = session.get(MCX_WARMUP_URL, headers=_HTML_GET_HEADERS, timeout=30)
        warmup_status_code = warmup_resp.status_code
    except requests.RequestException:
        pass  # still attempt the POST below with whatever cookies exist

    payload = json.dumps({"Date": day.strftime("%Y%m%d"), "InstrumentName": instrument})
    try:
        resp = session.post(MCX_BHAVCOPY_URL, headers=_API_POST_HEADERS, data=payload, timeout=30)
    except requests.RequestException as exc:
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

    This collapses a real failure (blocked, timed out, wrong shape) and a
    genuine "MCX has nothing today" into the same `None` -- by design, for
    the backfill loop, where a resumable day-by-day run needs to treat both
    the same way (cache an empty day, move on). Use `--probe` (which calls
    `_fetch_live_json_raw` directly) to tell those two cases apart.
    """
    result = _fetch_live_json_raw(day, instrument)
    if result.json_body is None:
        return None
    rows = (result.json_body or {}).get("d", {}).get("Data")
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
        records = ((result.json_body or {}).get("d", {}) or {}).get("Data")
        if not records:
            print(f"HTTP {result.status_code}, but no rows in the response for {day} "
                  "(likely a genuine holiday/not-yet-published day -- try a recent "
                  "weekday, or inspect the full response below).", file=sys.stderr)
            print(json.dumps(result.json_body, indent=2)[:2000], file=sys.stderr)
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
