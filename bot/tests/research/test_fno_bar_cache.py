"""Tests for research.fno.bar_cache -- the local full-OHLCV store for the
NSE F&O universe.

Two properties carry the design:

  * **Full OHLCV, not close-only.** The existing equity cache
    (`research/smallcap_momentum/price_data.py`) keeps only
    `date, close, volume`. Every config this store exists to serve is an ATR
    system (`atr_period=14`, `initial_stop_atr=2`, `trail_atr=3`), and ATR is
    a function of high and low. A close-only store silently cannot run them.

  * **The trading date is the IST calendar date.** Dhan stamps a daily bar
    `18:30:00+00:00` -- which is midnight IST of the NEXT day. Taking
    `.date()` off the raw UTC timestamp, as the small-cap cache does, shifts
    every date back by one. Confirmed on both existing caches by day-of-week
    counts: 249 "Sundays" and 1 "Friday" in a five-year daily series.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd

from research.fno import bar_cache


def _bar(ts: datetime, o=100.0, h=105.0, low=99.0, c=104.0, v=1000.0):
    return SimpleNamespace(timestamp=ts, open=o, high=h, low=low, close=c, volume=v)


def test_a_bar_stamped_1830_utc_belongs_to_the_next_ist_calendar_day():
    # 2021-09-05 18:30 UTC == 2021-09-06 00:00 IST. Sept 6 2021 was a Monday;
    # Sept 5 was a Sunday, and NSE does not trade on Sundays.
    ts = datetime(2021, 9, 5, 18, 30, tzinfo=timezone.utc)
    assert bar_cache.trading_date(ts) == date(2021, 9, 6)


def test_the_ist_conversion_removes_the_phantom_weekend_sessions():
    # A run of consecutive daily bars must land on weekdays, not weekends.
    start = datetime(2021, 9, 5, 18, 30, tzinfo=timezone.utc)  # -> Mon 6th IST
    dates = [bar_cache.trading_date(start + timedelta(days=i)) for i in range(5)]
    assert [d.weekday() for d in dates] == [0, 1, 2, 3, 4]


def test_the_store_round_trips_high_and_low_not_just_close():
    frame = bar_cache.bars_to_frame(
        [_bar(datetime(2024, 1, 2, 18, 30, tzinfo=timezone.utc), 10.0, 12.5, 9.5, 11.0, 42.0)]
    )
    assert list(frame.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    restored = bar_cache.frame_to_bars(frame)[0]
    assert (restored.high, restored.low) == (12.5, 9.5)
    assert (restored.open, restored.close, restored.volume) == (10.0, 11.0, 42.0)


def test_saving_then_loading_preserves_the_series(tmp_path):
    bars = [
        _bar(datetime(2024, 1, 1, 18, 30, tzinfo=timezone.utc), c=101.0),
        _bar(datetime(2024, 1, 2, 18, 30, tzinfo=timezone.utc), c=102.0),
        _bar(datetime(2024, 1, 3, 18, 30, tzinfo=timezone.utc), c=103.0),
    ]
    bar_cache.save("TESTCO", bar_cache.bars_to_frame(bars), cache_dir=tmp_path)
    assert bar_cache.is_cached("TESTCO", cache_dir=tmp_path)
    loaded = bar_cache.load("TESTCO", cache_dir=tmp_path)
    assert [b.close for b in loaded] == [101.0, 102.0, 103.0]


def test_loading_clips_to_the_requested_ist_window(tmp_path):
    bars = [_bar(datetime(2024, 1, d, 18, 30, tzinfo=timezone.utc), c=float(d)) for d in (1, 2, 3)]
    bar_cache.save("TESTCO", bar_cache.bars_to_frame(bars), cache_dir=tmp_path)
    # The bar stamped Jan 1 18:30 UTC trades on Jan 2 IST, so a window
    # starting Jan 3 IST must keep the Jan 2 and Jan 3 UTC stamps.
    loaded = bar_cache.load(
        "TESTCO", from_date=date(2024, 1, 3), to_date=date(2024, 1, 4), cache_dir=tmp_path
    )
    assert [b.close for b in loaded] == [2.0, 3.0]


def test_a_missing_symbol_says_how_to_populate_it(tmp_path):
    try:
        bar_cache.load("NOPE", cache_dir=tmp_path)
    except FileNotFoundError as exc:
        assert "research.fno.fetch_bars" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")


def test_the_frame_is_sorted_by_time_regardless_of_input_order():
    bars = [
        _bar(datetime(2024, 1, 3, 18, 30, tzinfo=timezone.utc), c=3.0),
        _bar(datetime(2024, 1, 1, 18, 30, tzinfo=timezone.utc), c=1.0),
        _bar(datetime(2024, 1, 2, 18, 30, tzinfo=timezone.utc), c=2.0),
    ]
    frame = bar_cache.bars_to_frame(bars)
    assert list(frame["close"]) == [1.0, 2.0, 3.0]
    assert pd.api.types.is_datetime64_any_dtype(pd.to_datetime(frame["timestamp"]))


# --- corporate-action screen --------------------------------------------


def test_an_unadjusted_split_sized_gap_is_flagged():
    from research.fno.fetch_bars import extreme_moves

    # A 1:5 split, unadjusted, looks like an -80% day.
    bars = [
        _bar(datetime(2021, 10, 27, 18, 30, tzinfo=timezone.utc), c=3000.0),
        _bar(datetime(2021, 10, 28, 18, 30, tzinfo=timezone.utc), c=600.0),
        _bar(datetime(2021, 10, 29, 18, 30, tzinfo=timezone.utc), c=610.0),
    ]
    flagged = extreme_moves(bars)
    assert len(flagged) == 1
    # Reported against the IST trading date, not the raw UTC stamp.
    assert flagged[0][0] == "2021-10-29"
    assert flagged[0][1] < -1.0


def test_ordinary_volatility_is_not_flagged():
    from research.fno.fetch_bars import extreme_moves

    bars = [
        _bar(datetime(2024, 1, d, 18, 30, tzinfo=timezone.utc), c=c)
        for d, c in [(1, 100.0), (2, 110.0), (3, 96.0), (4, 105.0)]
    ]
    assert extreme_moves(bars) == []
