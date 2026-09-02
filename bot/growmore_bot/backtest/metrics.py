"""Backtest performance metrics.

All functions are pure and take plain lists/numbers so they're trivial to
unit test with hand-computed known-answer cases (see
tests/unit/test_backtest_metrics.py).
"""
from __future__ import annotations

import math
from typing import Sequence


def sharpe_ratio(returns: Sequence[float], periods_per_year: int = 252) -> float:
    """Annualized Sharpe ratio (risk-free rate assumed 0) of a return series.

    Uses population standard deviation. Returns 0.0 for fewer than 2 data
    points or zero volatility (undefined Sharpe -> treated as "no edge").
    """
    n = len(returns)
    if n == 0:
        return 0.0
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / n
    stdev = math.sqrt(variance)
    if stdev == 0:
        return 0.0
    return (mean / stdev) * math.sqrt(periods_per_year)


def max_drawdown_pct(equity_curve: Sequence[float]) -> float:
    """Largest peak-to-trough decline, as a positive percentage."""
    if len(equity_curve) < 2:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for value in equity_curve:
        if value > peak:
            peak = value
        if peak > 0:
            drawdown = (peak - value) / peak * 100
            max_dd = max(max_dd, drawdown)
    return max_dd


def win_rate_pct(trade_pnls: Sequence[float]) -> float:
    """Percentage of trades with strictly positive PnL."""
    if not trade_pnls:
        return 0.0
    wins = sum(1 for pnl in trade_pnls if pnl > 0)
    return wins / len(trade_pnls) * 100


def profit_factor(trade_pnls: Sequence[float]) -> float:
    """Gross profit / gross loss. inf if there are wins and no losses; 0 if no trades."""
    if not trade_pnls:
        return 0.0
    gross_profit = sum(pnl for pnl in trade_pnls if pnl > 0)
    gross_loss = -sum(pnl for pnl in trade_pnls if pnl < 0)
    if gross_loss == 0:
        return math.inf if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def cagr_pct(start_equity: float, end_equity: float, years: float) -> float:
    """Compound annual growth rate, as a percentage."""
    if years <= 0 or start_equity <= 0:
        return 0.0
    return ((end_equity / start_equity) ** (1 / years) - 1) * 100


__all__ = ["sharpe_ratio", "max_drawdown_pct", "win_rate_pct", "profit_factor", "cagr_pct"]
