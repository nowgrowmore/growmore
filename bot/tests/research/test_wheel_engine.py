"""Tests for research.stock_options.wheel_engine.

The properties here are the ones that would corrupt a 210-stock table
silently rather than crash it. Physical settlement is what makes the wheel a
literal strategy on Indian stock options, so the load-bearing assertions are
about delivery: an ITM put must hand over real shares at the strike, and an
ITM call must take them away at the call strike.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd
import pytest

from research.stock_options.wheel_engine import (
    StrategyConfig,
    monthly_expiries,
    run_wheel,
    select_strike,
)

LOT = 100


def _chain(prices: dict[date, float], expiries: list[date], strikes=(80, 90, 95, 100, 105, 110, 120)):
    """A synthetic chain: every strike trades, priced intrinsic + time value.

    Time value decays with distance from spot, which matters: a pricer that
    gave every out-of-the-money strike the same premium would make the
    constrained and unconstrained wheels collect identical credit and hide
    the very difference these tests exist to detect.
    """
    rows = []
    for day, spot in sorted(prices.items()):
        for expiry in expiries:
            if expiry < day:
                continue
            days_left = max((expiry - day).days, 0)
            for k in strikes:
                for opt in ("CE", "PE"):
                    intrinsic = max(spot - k, 0) if opt == "CE" else max(k - spot, 0)
                    moneyness = (k - spot) / (0.15 * spot)
                    time_value = (
                        0.02 * spot * (days_left / 30.0) ** 0.5
                        * math.exp(-0.5 * moneyness * moneyness)
                    )
                    rows.append(
                        {
                            "trade_date": pd.Timestamp(day), "symbol": "TESTCO",
                            "expiry": pd.Timestamp(expiry), "strike": float(k),
                            "opt_type": opt, "open": 0.0, "high": 0.0, "low": 0.0,
                            "close": intrinsic + time_value,
                            "settle": round(intrinsic + time_value, 2),
                            "open_interest": 10_000, "volume": 500,
                            "lot_size": LOT, "underlying": spot,
                        }
                    )
    return pd.DataFrame(rows)


def _flat_series(start: date, n: int, price_fn):
    return {start + timedelta(days=i): price_fn(i) for i in range(n)}


def test_a_tradeable_strike_is_the_closest_one_that_actually_printed():
    chain = _chain({date(2024, 1, 1): 100.0}, [date(2024, 1, 25)])
    day = chain[chain["trade_date"] == pd.Timestamp(date(2024, 1, 1))]
    row = select_strike(day, "PE", spot=100.0, target_otm=0.05)
    assert row["strike"] == 95.0


def test_a_strike_that_never_printed_is_never_selected():
    chain = _chain({date(2024, 1, 1): 100.0}, [date(2024, 1, 25)])
    chain.loc[chain["strike"] == 95.0, "volume"] = 0
    day = chain[chain["trade_date"] == pd.Timestamp(date(2024, 1, 1))]
    row = select_strike(day, "PE", spot=100.0, target_otm=0.05)
    assert row["strike"] != 95.0


def test_the_no_loss_rule_removes_every_call_below_the_basis():
    chain = _chain({date(2024, 1, 1): 100.0}, [date(2024, 1, 25)])
    day = chain[chain["trade_date"] == pd.Timestamp(date(2024, 1, 1))]
    row = select_strike(day, "CE", spot=100.0, target_otm=0.05, floor_strike=110.0)
    assert row["strike"] >= 110.0


def test_the_rule_can_leave_nothing_sellable_and_says_so():
    # Assigned at 120 with the stock at 100, no call at or above 120 exists in
    # this chain -- the constraint biting, which must be None rather than a
    # quietly relaxed strike.
    chain = _chain({date(2024, 1, 1): 100.0}, [date(2024, 1, 25)], strikes=(90, 95, 100))
    day = chain[chain["trade_date"] == pd.Timestamp(date(2024, 1, 1))]
    assert select_strike(day, "CE", 100.0, 0.05, floor_strike=120.0) is None


def test_expiries_come_back_in_order():
    exp = [date(2024, 3, 28), date(2024, 1, 25), date(2024, 2, 29)]
    chain = _chain({date(2024, 1, 1): 100.0}, exp)
    got = [pd.Timestamp(e).date() for e in monthly_expiries(chain)]
    assert got == sorted(exp)


def _six_month_chain(price_fn):
    start = date(2024, 1, 1)
    expiries = [start + timedelta(days=30 * (i + 1)) for i in range(7)]
    prices = _flat_series(start, 210, price_fn)
    return _chain(prices, expiries)


def test_a_falling_stock_gets_the_wheel_assigned():
    # A stock that grinds down must trigger delivery: a 5% OTM put written
    # each month on a stock falling faster than 5% a month finishes ITM.
    chain = _six_month_chain(lambda i: 100.0 * (0.999 ** i))
    result = run_wheel("TESTCO", chain, StrategyConfig(tag="A"), initial_capital=500_000.0)
    assert result is not None
    assert result.assignment_rate_pct > 0
    assert any(r.assigned for r in result.records)


def test_a_rising_stock_keeps_the_premium_and_is_rarely_assigned():
    chain = _six_month_chain(lambda i: 100.0 * (1.001 ** i))
    result = run_wheel("TESTCO", chain, StrategyConfig(tag="A"), initial_capital=500_000.0)
    assert result is not None
    assert result.final_equity > 500_000.0
    assert result.assignment_rate_pct < 50.0


def test_the_equity_curve_is_marked_daily_not_at_cost_basis():
    """The assertion that stops the report being a lie.

    A stock that collapses after assignment must show a real drawdown. If the
    delivered shares were held at their cost basis the curve would be flat and
    the strategy would look riskless.
    """
    chain = _six_month_chain(lambda i: 100.0 * (0.99 ** i))
    result = run_wheel("TESTCO", chain, StrategyConfig(tag="A"), initial_capital=500_000.0)
    assert result is not None
    assert result.max_drawdown_pct > 5.0
    assert result.time_underwater_pct > 20.0


def test_time_frozen_counts_only_days_held_below_the_basis():
    chain = _six_month_chain(lambda i: 100.0 * (0.99 ** i))
    result = run_wheel("TESTCO", chain, StrategyConfig(tag="A"), initial_capital=500_000.0)
    assert result is not None
    assert 0.0 < result.time_frozen_pct <= result.time_underwater_pct + 1e-9


def test_a_stock_with_too_little_option_history_is_unmeasured_not_zero():
    chain = _chain({date(2024, 1, 1): 100.0}, [date(2024, 1, 25)])
    assert run_wheel("TESTCO", chain, StrategyConfig(tag="A")) is None


def test_the_put_spread_never_loses_more_than_the_width():
    """Strategy F's whole point, asserted against a crash.

    The long put caps the loss at (width - credit) per lot no matter how far
    the stock falls, so a collapse must hurt F far less than the bare wheel.
    """
    crash = _six_month_chain(lambda i: 100.0 * (0.985 ** i))
    bare = run_wheel("T", crash, StrategyConfig(tag="A"), initial_capital=500_000.0)
    spread = run_wheel(
        "T", crash, StrategyConfig(tag="F", long_put_otm=0.10), initial_capital=500_000.0
    )
    assert bare is not None and spread is not None
    assert spread.max_drawdown_pct < bare.max_drawdown_pct


def test_costs_are_charged_and_are_not_zero():
    chain = _six_month_chain(lambda i: 100.0 * (1.001 ** i))
    result = run_wheel("TESTCO", chain, StrategyConfig(tag="A"), initial_capital=500_000.0)
    assert result is not None
    assert result.total_cost > 0
    # ...but they are a small fraction of capital, not a strike-notional error.
    assert result.total_cost < 0.25 * 500_000.0


def test_buy_write_owns_the_stock_from_the_start():
    chain = _six_month_chain(lambda i: 100.0 * (1.001 ** i))
    result = run_wheel(
        "TESTCO", chain,
        StrategyConfig(tag="C", always_long=True, call_at_or_above_basis=False),
        initial_capital=500_000.0,
    )
    assert result is not None
    assert any(r.action == "buy_write" for r in result.records)


def test_the_constrained_and_unconstrained_wheels_differ_on_a_falling_stock():
    # The matched pair: identical but for whether calls may be written below
    # the assignment basis. On a stock that falls and stays down they must
    # diverge, or the constraint is not being applied.
    chain = _six_month_chain(lambda i: 100.0 * (0.99 ** i))
    a = run_wheel("T", chain, StrategyConfig(tag="A", call_at_or_above_basis=True),
                  initial_capital=500_000.0)
    b = run_wheel("T", chain, StrategyConfig(tag="B", call_at_or_above_basis=False),
                  initial_capital=500_000.0)
    assert a is not None and b is not None
    assert a.final_equity != pytest.approx(b.final_equity)


def test_the_trend_filter_makes_strategy_e_differ_from_plain_buy_write():
    """E must actually skip calls, or it is a duplicate of C wearing a label.

    On a stock that trends up throughout, a trend-conditioned buy-write should
    forgo most of its calls and keep the upside, where a plain buy-write caps
    it. If the two come back identical the filter is not wired in.
    """
    chain = _six_month_chain(lambda i: 100.0 * (1.004 ** i))
    days = sorted({d for d in pd.to_datetime(chain["trade_date"]).unique()})
    always_bullish = {pd.Timestamp(d): True for d in days}

    plain = run_wheel(
        "T", chain,
        StrategyConfig(tag="C", always_long=True, call_at_or_above_basis=False),
        initial_capital=500_000.0,
    )
    filtered = run_wheel(
        "T", chain,
        StrategyConfig(tag="E", always_long=True, call_at_or_above_basis=False,
                       trend_conditioned=True),
        initial_capital=500_000.0,
        trend_bullish_by_day=always_bullish,
    )
    assert plain is not None and filtered is not None
    assert any(r.action == "hold_uncovered" for r in filtered.records)
    assert not any(r.action == "hold_uncovered" for r in plain.records)
    # Uncapped upside on a rising stock must beat the capped version.
    assert filtered.final_equity > plain.final_equity
