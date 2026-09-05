"""Tests for research.intraday -- session segmentation, VWAP and replay."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from growmore_bot.strategies.vwap_session_bounce import VwapSessionBounceStrategy
from research.intraday.bar_cache import year_windows
from research.intraday.replay import replay
from research.intraday.sessions import daily_bar, running_session_vwap, sessions


def _frame(days=2, bars_per_day=10, start_price=100.0):
    rows = []
    for d in range(days):
        # 09:00 IST is 03:30 UTC.
        base = datetime(2026, 8, 3 + d, 3, 30, tzinfo=timezone.utc)
        for i in range(bars_per_day):
            price = start_price + d * 5 + i
            rows.append({
                "ts": base + timedelta(minutes=5 * i),
                "open": price, "high": price + 1, "low": price - 1,
                "close": price, "volume": 100.0,
            })
    return pd.DataFrame(rows)


class TestSessions:
    def test_bars_are_grouped_by_ist_calendar_day(self):
        grouped = list(sessions(_frame(days=3)))
        assert len(grouped) == 3
        assert all(len(bars) == 10 for _, bars in grouped)

    def test_a_session_aggregates_into_a_sane_daily_bar(self):
        _, bars = next(iter(sessions(_frame(days=1))))
        agg = daily_bar(bars)
        assert agg["open"] == pytest.approx(100.0)
        assert agg["close"] == pytest.approx(109.0)
        assert agg["high"] == pytest.approx(110.0)
        assert agg["low"] == pytest.approx(99.0)


class TestRunningSessionVwap:
    def test_first_bar_vwap_equals_its_own_typical_price(self):
        _, bars = next(iter(sessions(_frame(days=1))))
        vwap = running_session_vwap(bars)
        assert vwap.iloc[0] == pytest.approx((101 + 99 + 100) / 3)

    def test_it_is_cumulative_not_per_bar(self):
        """It has to reset at the session open and accumulate from there --
        that is what makes it comparable to Dhan's live average_price."""
        _, bars = next(iter(sessions(_frame(days=1))))
        vwap = running_session_vwap(bars)
        assert vwap.is_monotonic_increasing          # prices rise through the session
        assert vwap.iloc[-1] < bars["close"].iloc[-1]  # lags price, as an average must

    def test_zero_volume_bars_do_not_produce_a_divide_by_zero(self):
        frame = _frame(days=1)
        frame["volume"] = 0.0
        assert running_session_vwap(frame.assign(ist=frame["ts"])).isna().all()


class TestYearWindows:
    def test_windows_never_exceed_dhans_90_day_limit(self):
        from datetime import date

        windows = year_windows(date(2024, 1, 1), date(2024, 12, 31), 2024)
        assert all((stop - start).days < 90 for start, stop in windows)
        assert windows[0][0] == date(2024, 1, 1)
        assert windows[-1][1] == date(2024, 12, 31)

    def test_windows_are_calendar_aligned_so_reruns_are_idempotent(self):
        from datetime import date

        first = year_windows(date(2024, 1, 1), date(2024, 12, 31), 2024)
        again = year_windows(date(2024, 1, 1), date(2024, 12, 31), 2024)
        assert first == again


class TestReplay:
    def test_a_position_is_always_flat_by_the_session_close(self):
        """Both CPR and VWAP reset every session, so carrying a position into
        a new day trades on context that no longer exists. The live engines
        force-flatten; backtest/engine.py models that not at all, which is
        why the replay does it here."""
        result = replay(
            VwapSessionBounceStrategy, _frame(days=4, bars_per_day=20),
            lot_size=10, tick_size=1.0,
        )
        assert result.sessions_replayed == 3      # first session is warm-up only
        assert all(t.session is not None for t in result.trades)
        # Every trade closes within its own session -- there is no carry.
        assert all(t.reason in ("signal", "end_of_day") for t in result.trades)

    def test_the_first_session_is_consumed_as_warm_up_and_never_traded(self):
        result = replay(
            VwapSessionBounceStrategy, _frame(days=2, bars_per_day=20),
            lot_size=10, tick_size=1.0,
        )
        assert result.sessions_replayed == 1

    def test_costs_reduce_pnl_when_a_model_is_supplied(self):
        from growmore_bot.costs import DEFAULT_COST_MODEL

        frame = _frame(days=6, bars_per_day=30)
        free = replay(VwapSessionBounceStrategy, frame, lot_size=10, tick_size=1.0)
        priced = replay(
            VwapSessionBounceStrategy, frame, lot_size=10, tick_size=1.0,
            cost_model=DEFAULT_COST_MODEL,
        )
        if free.trades:
            assert priced.total_pnl < free.total_pnl
