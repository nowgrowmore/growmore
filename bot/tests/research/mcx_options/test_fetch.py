"""Tests for research.mcx_options.fetch.

MCX has no static-URL daily archive (unlike NSE) and the real bhavcopy page
is a scrape-only ASP.NET UI, so this module must never make a real network
call during a unit test. `fetch_day` and `fetch_and_cache_day` both take an
injectable fetcher function, and every test here injects a fake one returning
a canned CSV response instead of touching the network.
"""
from __future__ import annotations

from datetime import date

from research.mcx_options import chain_cache, fetch
from research.mcx_options.bhavcopy import MCXOptionRow

HEADER = (
    "SYMBOL,EXPIRY,STRIKE,OPTIONTYPE,OPEN,HIGH,LOW,CLOSE,PREVIOUS_CLOSE,"
    "VOLUME,VALUE,OPEN_INT"
)

CANNED_CSV = "\n".join(
    [
        HEADER,
        "GOLDM,26-SEP-2026,72000,CE,850.00,920.00,800.00,880.00,845.00,"
        "1250,1100000.50,4200",
        "SILVERM,30-NOV-2026,95000,PE,1200.00,1300.00,1150.00,1250.00,1180.00,"
        "300,375000.00,900",
    ]
)


def _fake_fetcher_returning(csv_text):
    calls = []

    def fetcher(day):
        calls.append(day)
        return csv_text

    fetcher.calls = calls
    return fetcher


def test_fetch_day_never_touches_the_network_and_uses_the_injected_fetcher():
    day = date(2026, 9, 4)
    fetcher = _fake_fetcher_returning(CANNED_CSV)

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
    fetcher = _fake_fetcher_returning(CANNED_CSV)

    count = fetch.fetch_and_cache_day(day, fetcher=fetcher)

    assert count == 2
    assert chain_cache.is_day_cached(day)
    import pandas as pd

    saved = pd.read_parquet(chain_cache._day_path(day))
    assert len(saved) == 2
    assert set(saved["symbol"]) == {"GOLDM", "SILVERM"}


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
