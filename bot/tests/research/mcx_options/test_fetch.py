"""Tests for research.mcx_options.fetch.

The real fetch mechanism (see the module docstring in
research/mcx_options/fetch.py) is a `curl_cffi`-driven JSON GET against
`market-data/bhavcopy/GetDateWiseBhavCopy`, confirmed live via a HAR capture
of a real browser session -- `curl_cffi` impersonates a real Chrome TLS
handshake, which is what gets past mcxindia.com's Akamai Bot Manager where
plain `requests`/`curl` cannot. This module must never make a real network
call during a unit test regardless -- `fetch_day` and `fetch_and_cache_day`
both take an injectable fetcher function, and every test here injects a
fake one returning canned JSON records (a list of dicts, matching the shape
`_fetch_live_json` would return) instead of touching the network.
"""
from __future__ import annotations

from datetime import date

from research.mcx_options import chain_cache, fetch
from research.mcx_options.bhavcopy import MCXOptionRow

CANNED_RECORDS = [
    {
        "Symbol": "GOLDM",
        "ExpiryDate": "26-SEP-2026",
        "StrikePrice": 72000,
        "OptionType": "CE",
        "Open": 850.00,
        "High": 920.00,
        "Low": 800.00,
        "Close": 880.00,
        "PreviousClose": 845.00,
        "Volume": 1250,
        "Value": 1100000.50,
        "OpenInterest": 4200,
    },
    {
        "Symbol": "SILVERM",
        "ExpiryDate": "30-NOV-2026",
        "StrikePrice": 95000,
        "OptionType": "PE",
        "Open": 1200.00,
        "High": 1300.00,
        "Low": 1150.00,
        "Close": 1250.00,
        "PreviousClose": 1180.00,
        "Volume": 300,
        "Value": 375000.00,
        "OpenInterest": 900,
    },
]


def _fake_fetcher_returning(records):
    calls = []

    def fetcher(day):
        calls.append(day)
        return records

    fetcher.calls = calls
    return fetcher


def test_fetch_day_never_touches_the_network_and_uses_the_injected_fetcher():
    day = date(2026, 9, 4)
    fetcher = _fake_fetcher_returning(CANNED_RECORDS)

    rows = fetch.fetch_day(day, fetcher=fetcher)

    assert fetcher.calls == [day]
    assert all(isinstance(r, MCXOptionRow) for r in rows)
    assert len(rows) == 2
    assert {r.symbol for r in rows} == {"GOLDM", "SILVERM"}
    assert all(r.trade_date == day for r in rows)


def test_fetch_day_returns_empty_list_when_fetcher_has_nothing():
    day = date(2026, 9, 5)
    fetcher = _fake_fetcher_returning(None)

    rows = fetch.fetch_day(day, fetcher=fetcher)

    assert rows == []


def test_fetch_and_cache_day_flows_through_to_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(chain_cache, "DAY_DIR", tmp_path / "days")
    monkeypatch.setattr(chain_cache, "SYMBOL_DIR", tmp_path / "symbols")

    day = date(2026, 9, 4)
    fetcher = _fake_fetcher_returning(CANNED_RECORDS)

    count = fetch.fetch_and_cache_day(day, fetcher=fetcher)

    assert count == 2
    assert chain_cache.is_day_cached(day)
    import pandas as pd

    saved = pd.read_parquet(chain_cache._day_path(day))
    assert len(saved) == 2
    assert set(saved["symbol"]) == {"GOLDM", "SILVERM"}


# --- _fetch_live_json (the real network path) -- HTTP layer fully mocked,
# never a real network call, per this repo's testing convention. ---


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None, text=""):
        self.ok = 200 <= status_code < 300
        self.status_code = status_code
        self._json_body = json_body
        self.text = text

    def json(self):
        if self._json_body is None:
            raise ValueError("no JSON body")
        return self._json_body


class _FakeCurlCffiException(Exception):
    """Stands in for `curl_cffi.requests.exceptions.RequestException`."""


class _FakeExceptionsModule:
    RequestException = _FakeCurlCffiException


def _fake_session(get_fn, warmup_status_code=200):
    """A fake `curl_cffi.requests.Session` whose warm-up `.get` (the FIRST
    call `_fetch_live_json_raw` makes, to `MCX_WARMUP_URL`) succeeds by
    default, and whose data `.get` (the SECOND call, to `MCX_BHAVCOPY_URL`)
    is supplied per test via `get_fn`. Distinguishes the two by URL, exactly
    like the real code calls `.get` twice with different arguments.
    """

    class _FakeSession:
        def __init__(self, impersonate=None):
            self.impersonate = impersonate

        def get(self, url, headers, timeout, params=None):
            if url == fetch.MCX_WARMUP_URL:
                return _FakeResponse(warmup_status_code)
            return get_fn(url, params, headers, timeout)

    return _FakeSession


def _patch_curl_cffi(monkeypatch, session_cls):
    import curl_cffi.requests

    monkeypatch.setattr(curl_cffi.requests, "Session", session_cls)
    monkeypatch.setattr(curl_cffi.requests, "exceptions", _FakeExceptionsModule)


def test_fetch_live_json_gets_the_expected_request_and_returns_data(monkeypatch):
    captured = {}

    def get_fn(url, params, headers, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _FakeResponse(200, {"IsSuccess": True, "Data": CANNED_RECORDS})

    _patch_curl_cffi(monkeypatch, _fake_session(get_fn))

    result = fetch._fetch_live_json(date(2026, 9, 4))

    assert result == CANNED_RECORDS
    assert captured["url"] == fetch.MCX_BHAVCOPY_URL
    assert captured["params"] == {"InstrumentName": "ALL", "fromDate": "04/09/2026"}
    assert captured["headers"]["X-Requested-With"] == "XMLHttpRequest"


def test_fetch_live_json_returns_none_on_http_failure(monkeypatch):
    _patch_curl_cffi(monkeypatch, _fake_session(lambda *a, **k: _FakeResponse(403)))

    assert fetch._fetch_live_json(date(2026, 9, 4)) is None


def test_fetch_live_json_returns_none_when_not_is_success(monkeypatch):
    _patch_curl_cffi(
        monkeypatch,
        _fake_session(lambda *a, **k: _FakeResponse(200, {"IsSuccess": False, "Message": "No data found."})),
    )

    assert fetch._fetch_live_json(date(2026, 9, 4)) is None


def test_fetch_live_json_returns_none_when_data_is_empty(monkeypatch):
    _patch_curl_cffi(
        monkeypatch,
        _fake_session(lambda *a, **k: _FakeResponse(200, {"IsSuccess": True, "Data": []})),
    )

    assert fetch._fetch_live_json(date(2026, 9, 4)) is None


# --- _fetch_live_json_raw -- the un-swallowed diagnostic path `--probe`
# uses, so a real failure and a genuine empty day never look the same. ---


def test_fetch_live_json_raw_reports_the_exception(monkeypatch):
    def get_fn(url, params, headers, timeout):
        raise _FakeCurlCffiException("boom")

    _patch_curl_cffi(monkeypatch, _fake_session(get_fn))

    result = fetch._fetch_live_json_raw(date(2026, 9, 4))

    assert isinstance(result.exception, _FakeCurlCffiException)
    assert result.json_body is None
    assert result.warmup_status_code == 200


def test_fetch_live_json_raw_reports_http_failure_with_body(monkeypatch):
    _patch_curl_cffi(
        monkeypatch,
        _fake_session(lambda *a, **k: _FakeResponse(403, text="<HTML>Access Denied</HTML>")),
    )

    result = fetch._fetch_live_json_raw(date(2026, 9, 4))

    assert result.exception is None
    assert result.status_code == 403
    assert "Access Denied" in result.body_text
    assert result.json_body is None


def test_fetch_live_json_raw_reports_a_successful_but_empty_day(monkeypatch):
    _patch_curl_cffi(
        monkeypatch,
        _fake_session(lambda *a, **k: _FakeResponse(200, {"IsSuccess": True, "Data": []})),
    )

    result = fetch._fetch_live_json_raw(date(2026, 9, 4))

    assert result.exception is None
    assert result.status_code == 200
    assert result.json_body == {"IsSuccess": True, "Data": []}


def test_fetch_live_json_raw_still_gets_data_when_warmup_itself_fails(monkeypatch):
    """A non-2xx (or exception-raising) warm-up GET must not prevent the
    data GET from being attempted -- some of the cookies Akamai cares about
    can still land on a non-2xx response, and even if not, failing outright
    here would hide the more informative data-GET-level diagnostic from
    `--probe`.
    """
    _patch_curl_cffi(
        monkeypatch,
        _fake_session(
            lambda *a, **k: _FakeResponse(200, {"IsSuccess": True, "Data": CANNED_RECORDS}),
            warmup_status_code=403,
        ),
    )

    result = fetch._fetch_live_json_raw(date(2026, 9, 4))

    assert result.warmup_status_code == 403
    assert result.json_body == {"IsSuccess": True, "Data": CANNED_RECORDS}


# --- dates_in_range -- the sparse-cadence backfill helper. The engine only
# needs an options chain on the day it actually attempts an entry (settlement
# is decided from the futures price alone, no chain lookup); fetching every
# calendar day for a multi-year backfill is unnecessarily heavy, so `--every
# monday` (etc.) restricts the backfill to one weekday, which becomes "the
# engine can only enter on that weekday" -- a deliberate, documented
# simplification, not an accident of what data happens to exist. ---


def test_dates_in_range_defaults_to_every_day():
    days = fetch.dates_in_range(date(2026, 9, 1), date(2026, 9, 5))
    assert days == [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3),
                     date(2026, 9, 4), date(2026, 9, 5)]


def test_dates_in_range_filters_to_one_weekday():
    # 2026-09-01 is a Tuesday; Mondays in this window are 2026-09-07 and 09-14.
    days = fetch.dates_in_range(date(2026, 9, 1), date(2026, 9, 15), weekday=0)
    assert days == [date(2026, 9, 7), date(2026, 9, 14)]


def test_dates_in_range_weekday_matching_from_date_includes_it():
    # 2026-09-07 is itself a Monday -- must be included, not skipped.
    days = fetch.dates_in_range(date(2026, 9, 7), date(2026, 9, 7), weekday=0)
    assert days == [date(2026, 9, 7)]


def test_dates_in_range_empty_when_from_after_to():
    assert fetch.dates_in_range(date(2026, 9, 5), date(2026, 9, 1)) == []


def test_main_with_every_monday_only_fetches_mondays(tmp_path, monkeypatch):
    monkeypatch.setattr(chain_cache, "DAY_DIR", tmp_path / "days")
    monkeypatch.setattr(chain_cache, "SYMBOL_DIR", tmp_path / "symbols")

    fetched_days = []

    def fake_fetch_and_cache_day(day, fetcher=None):
        fetched_days.append(day)
        return 0

    monkeypatch.setattr(fetch, "fetch_and_cache_day", fake_fetch_and_cache_day)

    # 2026-09-01 (Tue) .. 2026-09-15 (Tue): Mondays in range are 09-07, 09-14.
    fetch.main(["--from", "2026-09-01", "--to", "2026-09-15", "--every", "monday"])

    assert fetched_days == [date(2026, 9, 7), date(2026, 9, 14)]


def test_fetch_and_cache_day_is_a_noop_write_when_fetcher_has_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(chain_cache, "DAY_DIR", tmp_path / "days")
    monkeypatch.setattr(chain_cache, "SYMBOL_DIR", tmp_path / "symbols")

    day = date(2026, 9, 6)
    fetcher = _fake_fetcher_returning(None)

    count = fetch.fetch_and_cache_day(day, fetcher=fetcher)

    assert count == 0
    # Still recorded as cached (empty), so a resumable backfill does not
    # re-fetch a day MCX genuinely has nothing for.
    assert chain_cache.is_day_cached(day)
