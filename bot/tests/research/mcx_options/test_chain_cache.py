"""Tests for research.mcx_options.chain_cache -- the day-parquet -> per-symbol
consolidation, adapted directly from research/stock_options/chain_cache.py's
design (same streaming/flush logic) but over MCXOptionRow objects.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from research.mcx_options import chain_cache
from research.mcx_options.bhavcopy import MCXOptionRow


def _row(symbol, day, strike, opt_type="CE", volume=10):
    return MCXOptionRow(
        trade_date=day,
        symbol=symbol,
        expiry=date(2026, 9, 26),
        strike=strike,
        opt_type=opt_type,
        open=100.0,
        high=110.0,
        low=90.0,
        close=105.0,
        previous_close=95.0,
        open_interest=500,
        volume=volume,
        value=1000.0,
    )


def test_rows_to_frame_round_trips_the_fields():
    rows = [_row("GOLDM", date(2026, 9, 1), 72000.0)]
    frame = chain_cache.rows_to_frame(rows)
    assert list(frame.columns) == chain_cache.COLUMNS
    assert frame.iloc[0]["symbol"] == "GOLDM"
    assert frame.iloc[0]["strike"] == 72000.0
    assert frame.iloc[0]["volume"] == 10


def test_save_and_load_one_day(tmp_path, monkeypatch):
    monkeypatch.setattr(chain_cache, "DAY_DIR", tmp_path / "days")
    monkeypatch.setattr(chain_cache, "SYMBOL_DIR", tmp_path / "symbols")

    day = date(2026, 9, 1)
    assert not chain_cache.is_day_cached(day)

    rows = [_row("GOLDM", day, 72000.0), _row("SILVERM", day, 95000.0)]
    chain_cache.save_day(day, chain_cache.rows_to_frame(rows))

    assert chain_cache.is_day_cached(day)
    assert chain_cache.cached_days() == [day]


def test_consolidate_by_symbol_writes_one_parquet_per_underlying(tmp_path, monkeypatch):
    monkeypatch.setattr(chain_cache, "DAY_DIR", tmp_path / "days")
    monkeypatch.setattr(chain_cache, "SYMBOL_DIR", tmp_path / "symbols")

    day1, day2 = date(2026, 9, 1), date(2026, 9, 2)
    chain_cache.save_day(
        day1,
        chain_cache.rows_to_frame(
            [_row("GOLDM", day1, 72000.0), _row("SILVERM", day1, 95000.0)]
        ),
    )
    chain_cache.save_day(
        day2, chain_cache.rows_to_frame([_row("GOLDM", day2, 73000.0)])
    )

    counts = chain_cache.consolidate_by_symbol()
    assert counts == {"GOLDM": 2, "SILVERM": 1}
    assert chain_cache.cached_symbols() == ["GOLDM", "SILVERM"]

    goldm = chain_cache.load_symbol("GOLDM")
    assert len(goldm) == 2
    assert set(pd.to_datetime(goldm["trade_date"]).dt.date) == {day1, day2}


def test_loading_an_uncached_symbol_says_how_to_fix_it(tmp_path, monkeypatch):
    monkeypatch.setattr(chain_cache, "DAY_DIR", tmp_path / "days")
    monkeypatch.setattr(chain_cache, "SYMBOL_DIR", tmp_path / "symbols")
    import pytest

    with pytest.raises(FileNotFoundError, match="research.mcx_options.fetch"):
        chain_cache.load_symbol("NOPE")
