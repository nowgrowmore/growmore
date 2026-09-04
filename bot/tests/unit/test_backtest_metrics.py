"""Known-answer tests for growmore_bot.backtest.metrics."""
from __future__ import annotations

import math

import pytest

from growmore_bot.backtest.metrics import (
    cagr_pct,
    max_drawdown_pct,
    profit_factor,
    sharpe_ratio,
    win_rate_pct,
)


def test_sharpe_ratio_known_returns():
    # Daily returns with mean 0.01, population stdev computed by hand.
    returns = [0.01, 0.02, -0.01, 0.03, 0.0]
    mean = sum(returns) / len(returns)  # 0.01
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    stdev = math.sqrt(variance)
    expected_daily_sharpe = mean / stdev
    expected_annualized = expected_daily_sharpe * math.sqrt(252)

    result = sharpe_ratio(returns, periods_per_year=252)
    assert result == pytest.approx(expected_annualized, rel=1e-9)


def test_sharpe_ratio_zero_stdev_returns_zero():
    assert sharpe_ratio([0.01, 0.01, 0.01], periods_per_year=252) == 0.0


def test_sharpe_ratio_empty_returns_zero():
    assert sharpe_ratio([], periods_per_year=252) == 0.0


def test_max_drawdown_pct_known_equity_curve():
    # Equity: 100 -> 120 (peak) -> 90 (trough, -25% from peak) -> 110
    equity_curve = [100, 120, 90, 110]
    # drawdown = (peak - trough) / peak * 100 = (120-90)/120*100 = 25.0
    assert max_drawdown_pct(equity_curve) == pytest.approx(25.0)


def test_max_drawdown_pct_monotonic_increase_is_zero():
    assert max_drawdown_pct([100, 110, 120, 130]) == pytest.approx(0.0)


def test_max_drawdown_pct_single_point_is_zero():
    assert max_drawdown_pct([100]) == pytest.approx(0.0)


def test_win_rate_pct_known_trades():
    pnls = [10, -5, 20, -1, 0]  # wins: 10, 20 (0 is not a win) -> 2/5 = 40%
    assert win_rate_pct(pnls) == pytest.approx(40.0)


def test_win_rate_pct_no_trades_is_zero():
    assert win_rate_pct([]) == pytest.approx(0.0)


def test_profit_factor_known_trades():
    pnls = [10, -5, 20, -10]  # gross profit=30, gross loss=15 -> pf=2.0
    assert profit_factor(pnls) == pytest.approx(2.0)


def test_profit_factor_no_losses_is_infinite():
    assert profit_factor([10, 20]) == math.inf


def test_profit_factor_no_trades_is_zero():
    assert profit_factor([]) == 0.0


def test_cagr_pct_known_case():
    # 100 -> 200 over exactly 2 years => CAGR = (2)^(1/2) - 1 = ~41.42%
    result = cagr_pct(start_equity=100, end_equity=200, years=2)
    assert result == pytest.approx((2 ** 0.5 - 1) * 100, rel=1e-9)


def test_cagr_pct_zero_years_is_zero():
    assert cagr_pct(start_equity=100, end_equity=200, years=0) == 0.0


def test_cagr_pct_wiped_out_account_is_minus_100_not_a_complex_number():
    """Regression (independent code review 2026-09-04): a negative end
    equity (entirely reachable here -- these are leveraged commodity lots
    against an unleveraged `initial_capital`, so a bad variant's equity
    curve can cross zero) hit `(negative) ** (1/years)`, which in Python
    returns a COMPLEX number rather than raising. That complex value flowed
    straight into BacktestRun.cagr_pct, so a blown-up run either crashed the
    whole sweep at DB-write time or persisted garbage that ranked
    unpredictably. A wiped-out account is -100%: there's no real compound
    rate below "lost everything".
    """
    result = cagr_pct(start_equity=100_000, end_equity=-50_000, years=5)
    assert isinstance(result, float)
    assert result == pytest.approx(-100.0)
    assert cagr_pct(start_equity=100_000, end_equity=0.0, years=5) == pytest.approx(-100.0)
