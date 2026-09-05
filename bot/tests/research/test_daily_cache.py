"""Tests for research.dailydata.cache -- the round trip and the date clip."""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from research.dailydata import cache


def _bar(day, o, h, low, c, v=100.0):
    return SimpleNamespace(
        timestamp=datetime(2024, 1, day), open=o, high=h, low=low, close=c, volume=v
    )


def test_bars_round_trip_through_a_frame_unchanged():
    bars = [_bar(1, 10, 12, 9, 11), _bar(2, 11, 13, 10, 12)]
    restored = cache.frame_to_bars(cache.bars_to_frame(bars))

    assert [b.timestamp for b in restored] == [datetime(2024, 1, 1), datetime(2024, 1, 2)]
    assert [b.close for b in restored] == [11.0, 12.0]
    assert [b.high for b in restored] == [12.0, 13.0]


def test_bars_are_sorted_by_timestamp_even_if_the_source_was_not():
    frame = cache.bars_to_frame([_bar(3, 1, 1, 1, 30), _bar(1, 1, 1, 1, 10)])
    assert list(frame["close"]) == [10.0, 30.0]


def test_save_and_load_clips_to_the_requested_window(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    bars = [_bar(d, 1, 1, 1, float(d)) for d in (1, 2, 3, 4, 5)]
    cache.save("TESTSYM", cache.bars_to_frame(bars))

    assert cache.is_cached("TESTSYM")
    assert cache.cached_symbols() == ["TESTSYM"]

    clipped = cache.load("TESTSYM", from_date=date(2024, 1, 2), to_date=date(2024, 1, 4))
    assert [b.close for b in clipped] == [2.0, 3.0, 4.0]

    assert len(cache.load("TESTSYM")) == 5


def test_loading_an_uncached_symbol_says_how_to_fix_it(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="research.dailydata.fetch"):
        cache.load("NOPE")


def test_a_cached_bar_duck_types_what_the_backtest_engine_reads():
    """The engine only touches these five attributes; if that ever changes
    this test is the tripwire."""
    bar = cache.frame_to_bars(cache.bars_to_frame([_bar(1, 10, 12, 9, 11)]))[0]
    for attr in ("timestamp", "open", "high", "low", "close"):
        assert hasattr(bar, attr)
