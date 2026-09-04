"""Tests for research.smallcap_momentum.portfolio_engine -- the cross-
sectional rebalance/hold/mark-to-market simulator. Every scenario here is
hand-constructed so the expected selection, weights, and equity curve can be
verified by hand, the same rigor growmore_bot.backtest.engine's own tests
use (see that module's test file for the house style this follows).
"""
from __future__ import annotations

from datetime import date

import pytest

from research.smallcap_momentum.portfolio_engine import run_portfolio_backtest

D = [date(2026, 1, d) for d in range(1, 7)]  # D[0]..D[5], 6 trading days


def test_selects_the_clearly_stronger_momentum_stock_and_marks_to_market():
    # A trends up, B trends down -- A's momentum score is unambiguously
    # higher regardless of the exact volatility-adjustment arithmetic
    # (positive numerator vs. negative numerator, same-sign denominator).
    price_series = {
        "A": list(zip(D, [100, 102, 104, 106, 108, 110])),
        "B": list(zip(D, [100, 99, 98, 97, 96, 95])),
    }

    result = run_portfolio_backtest(
        price_series=price_series,
        fundamentals={},
        rebalance_dates=[D[4]],
        top_n=1,
        initial_capital=1000.0,
        use_quality=False,
        trend_filter=False,
        lookback_days_6m=2,
        lookback_days_12m=4,
    )

    assert len(result.rebalances) == 1
    assert result.rebalances[0].selected == ["A"]
    assert result.rebalances[0].weights == {"A": pytest.approx(1.0)}

    # units_A = (1.0 * 1000) / 108 (A's price at the rebalance date D[4])
    units_a = 1000.0 / 108.0
    expected_curve = [
        (D[4], pytest.approx(1000.0)),
        (D[5], pytest.approx(units_a * 110.0)),
    ]
    assert result.equity_curve == expected_curve
    assert result.final_equity == pytest.approx(units_a * 110.0)


def test_quality_breaks_a_momentum_tie():
    # Identical price series -> identical (tied) momentum scores. With
    # use_quality=True, the stock with the better fundamentals must win.
    identical_prices = [100, 100, 102, 104, 106, 108]
    price_series = {
        "GOOD_QUALITY": list(zip(D, identical_prices)),
        "BAD_QUALITY": list(zip(D, identical_prices)),
    }
    fundamentals = {
        "GOOD_QUALITY": (0.30, 0.2, 0.20),  # roe, debt_to_equity, eps_growth
        "BAD_QUALITY": (0.02, 3.0, -0.10),
    }

    result = run_portfolio_backtest(
        price_series=price_series,
        fundamentals=fundamentals,
        rebalance_dates=[D[4]],
        top_n=1,
        initial_capital=1000.0,
        use_quality=True,
        trend_filter=False,
        lookback_days_6m=2,
        lookback_days_12m=4,
    )

    assert result.rebalances[0].selected == ["GOOD_QUALITY"]


def test_trend_filter_excludes_a_stock_trading_below_its_own_moving_average():
    # C has a real, positive momentum score (climbs overall) but has pulled
    # back just before the rebalance, so its price sits below its own
    # trailing 3-day average -- verified: SMA3@D[4]=108.67, price[4]=108.
    # D_ climbs steadily and stays above its own SMA3 (103.0 vs price 104).
    # With top_n=2 and exactly 2 candidates, both are selected whenever
    # both are eligible -- isolating the filter's exclusion effect instead
    # of needing C to also out-rank D_ on raw momentum (a vol-adjusted
    # score structurally punishes C's pullback twice over -- lower trailing
    # return AND higher measured volatility -- so a winner-take-all
    # comparison isn't a reliable way to isolate just the filter).
    price_series = {
        "C": list(zip(D, [100, 102, 106, 112, 108, 108])),
        "D_": list(zip(D, [100, 101, 102, 103, 104, 105])),
    }

    without_filter = run_portfolio_backtest(
        price_series=price_series,
        fundamentals={},
        rebalance_dates=[D[4]],
        top_n=2,
        initial_capital=1000.0,
        use_quality=False,
        trend_filter=False,
        lookback_days_6m=2,
        lookback_days_12m=4,
    )
    assert set(without_filter.rebalances[0].selected) == {"C", "D_"}
    assert without_filter.rebalances[0].eligible_count == 2

    with_filter = run_portfolio_backtest(
        price_series=price_series,
        fundamentals={},
        rebalance_dates=[D[4]],
        top_n=2,
        initial_capital=1000.0,
        use_quality=False,
        trend_filter=True,
        lookback_days_6m=2,
        lookback_days_12m=4,
        trend_sma_days=3,
    )
    assert with_filter.rebalances[0].selected == ["D_"]
    assert with_filter.rebalances[0].eligible_count == 1


def test_a_rebalance_with_zero_eligible_stocks_holds_cash_not_zero():
    # Regression, found running the real 5-year backtest (2026-09-04): the
    # very first rebalance can have zero stocks with enough trailing
    # history yet (lookback_days_12m not satisfied for anyone). That must
    # mean "hold cash, unchanged" until a later rebalance can select
    # something -- NOT permanently zero out the whole equity curve, which
    # is what happened before this fix (every subsequent rebalance's
    # allocation multiplied its weight by an already-zeroed equity).
    price_series = {
        "A": list(zip(D, [100, 102, 104, 106, 108, 110])),
    }

    result = run_portfolio_backtest(
        price_series=price_series,
        fundamentals={},
        # D[1] is too early for anyone to have lookback_days_12m=4 of
        # history (only 1 prior bar exists) -- zero eligible. D[4] has
        # enough history and should select A normally.
        rebalance_dates=[D[1], D[4]],
        top_n=1,
        initial_capital=1000.0,
        use_quality=False,
        trend_filter=False,
        lookback_days_6m=2,
        lookback_days_12m=4,
    )

    assert result.rebalances[0].selected == []
    assert result.rebalances[0].eligible_count == 0
    assert result.rebalances[1].selected == ["A"]

    # Equity must stay at initial_capital (held as cash) through the first,
    # empty rebalance -- never drop to 0.
    for d, equity in result.equity_curve:
        if d < D[4]:
            assert equity == pytest.approx(1000.0)

    units_a = 1000.0 / 108.0
    assert result.equity_curve[-1] == (D[5], pytest.approx(units_a * 110.0))


def test_a_rebalance_date_not_on_the_trading_calendar_snaps_to_the_prior_trading_day():
    # Regression, found running the real 5-year backtest (2026-09-04): a
    # requested rebalance date (e.g. semiannual calendar boundaries like
    # 2021-12-31) that isn't literally a trading day in the union calendar
    # was silently skipped entirely instead of snapping to the nearest
    # prior trading day -- 3 of 10 intended rebalances vanished this way in
    # the real run.
    dates_with_a_gap = [D[0], D[1], D[3], D[4], D[5]]  # D[2] has no bar for anyone
    price_series = {
        "A": list(zip(dates_with_a_gap, [100, 102, 104, 106, 108])),
        "B": list(zip(dates_with_a_gap, [100, 99, 98, 97, 96])),
    }

    result = run_portfolio_backtest(
        price_series=price_series,
        fundamentals={},
        rebalance_dates=[D[2]],  # not a real trading day for anyone
        top_n=1,
        initial_capital=1000.0,
        use_quality=False,
        trend_filter=False,
        lookback_days_6m=1,
        lookback_days_12m=1,
    )

    assert len(result.rebalances) == 1
    # Snapped to D[1], the last real trading day on/before D[2].
    assert result.rebalances[0].date == D[1]


def test_insufficient_history_excludes_a_stock_from_eligibility():
    # SHORT_HISTORY only has 2 bars by the rebalance date -- fewer than
    # lookback_days_12m=4 -- so it can't have a momentum score computed and
    # must not be selectable, even with top_n=2 and only one other stock.
    price_series = {
        "ESTABLISHED": list(zip(D, [100, 101, 102, 103, 104, 105])),
        "SHORT_HISTORY": list(zip(D[3:], [50, 51, 52])),
    }

    result = run_portfolio_backtest(
        price_series=price_series,
        fundamentals={},
        rebalance_dates=[D[4]],
        top_n=2,
        initial_capital=1000.0,
        use_quality=False,
        trend_filter=False,
        lookback_days_6m=2,
        lookback_days_12m=4,
    )

    assert result.rebalances[0].selected == ["ESTABLISHED"]
    assert result.rebalances[0].weights == {"ESTABLISHED": pytest.approx(1.0)}
    assert result.rebalances[0].eligible_count == 1


def test_missing_price_on_a_held_day_carries_forward_the_last_known_close():
    # A has a gap at D[5] (no bar that day, e.g. a trading halt) -- the
    # portfolio must mark it to market at its last known price rather than
    # dropping it or crashing. A ticks up slightly (positive momentum); B
    # declines (negative) -- A wins clearly regardless of vol-adjustment
    # specifics (a dead-flat series would instead hit momentum_score's
    # zero-volatility None case, tested separately in test_scoring.py).
    price_series = {
        "A": [(D[0], 100), (D[1], 100), (D[2], 100), (D[3], 100), (D[4], 101)],
        "B": list(zip(D, [100, 99, 98, 97, 96, 95])),
    }

    result = run_portfolio_backtest(
        price_series=price_series,
        fundamentals={},
        rebalance_dates=[D[4]],
        top_n=1,
        initial_capital=1000.0,
        use_quality=False,
        trend_filter=False,
        lookback_days_6m=2,
        lookback_days_12m=4,
    )

    assert result.rebalances[0].selected == ["A"]
    # D[5] has no bar for A -- equity must carry forward A's D[4] price
    # (101), not crash or silently drop the day.
    units_a = 1000.0 / 101.0
    assert result.equity_curve[-1] == (D[5], pytest.approx(units_a * 101.0))
