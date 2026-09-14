"""Tests for research.mcx_options.fetch.

The real fetch mechanism (see the module docstring in
research/mcx_options/fetch.py) is a JSON POST to
`backpage.aspx/GetDateWiseBhavCopy`, discovered from the third-party
`mcxlib` package's source since mcxindia.com is unreachable (an Akamai edge
block) from this development environment. This module must never make a
real network call during a unit test regardless -- `fetch_day` and
`fetch_and_cache_day` both take an injectable fetcher function, and every
test here injects a fake one returning canned JSON records (a list of
dicts, matching the shape `_fetch_live_json` would return) instead of
touching the network.
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
    def __init__(self, status_code=200, json_body=None):
        self.ok = 200 <= status_code < 300
        self.status_code = status_code
        self._json_body = json_body

    def json(self):
        if self._json_body is None:
            raise ValueError("no JSON body")
        return self._json_body


def test_fetch_live_json_posts_the_expected_request_and_returns_data(monkeypatch):
    captured = {}

    class _FakeSession:
        def __init__(self):
            self.trust_env = True

        def post(self, url, headers, data, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["data"] = data
            captured["timeout"] = timeout
            return _FakeResponse(200, {"d": {"Data": CANNED_RECORDS}})

    import requests

    monkeypatch.setattr(requests, "Session", _FakeSession)

    result = fetch._fetch_live_json(date(2026, 9, 4))

    assert result == CANNED_RECORDS
    assert captured["url"] == fetch.MCX_BHAVCOPY_URL
    import json as _json

    assert _json.loads(captured["data"]) == {"Date": "20260904", "InstrumentName": "OPTFUT"}
    assert captured["headers"]["X-Requested-With"] == "XMLHttpRequest"


def test_fetch_live_json_returns_none_on_http_failure(monkeypatch):
    class _FakeSession:
        def __init__(self):
            self.trust_env = True

        def post(self, url, headers, data, timeout):
            return _FakeResponse(403)

    import requests

    monkeypatch.setattr(requests, "Session", _FakeSession)

    assert fetch._fetch_live_json(date(2026, 9, 4)) is None


def test_fetch_live_json_returns_none_when_data_is_empty(monkeypatch):
    class _FakeSession:
        def __init__(self):
            self.trust_env = True

        def post(self, url, headers, data, timeout):
            return _FakeResponse(200, {"d": {"Data": []}})

    import requests

    monkeypatch.setattr(requests, "Session", _FakeSession)

    assert fetch._fetch_live_json(date(2026, 9, 4)) is None


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
