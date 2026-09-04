"""Cross-sectional momentum(+quality) portfolio backtest.

Distinct from growmore_bot.backtest.engine.BacktestEngine, which simulates
ONE strategy on ONE instrument at a time: this ranks an entire universe of
stocks against each other at each rebalance date, holds an equal-weighted
basket of the top N until the next rebalance, and marks the whole basket to
market daily. See docs/smallcap-momentum-research.md and
docs/smallcap-momentum-backtest-results.md (once it exists) for the
strategy rationale and real results; see scoring.py for the documented
simplifications relative to NSE's own published momentum/quality indices.

Equal-weighting every selected name (rather than NSE's own free-float-
market-cap tilt) is a deliberate simplification that also directly avoids
the "1 lot / 1 unit regardless of capital" comparability bias flagged for
the commodity backtest sweep (docs/backtest-results.md) -- every name gets
the same rupee allocation, so cross-stock comparisons don't need a separate
position-sizing fix.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from research.smallcap_momentum.scoring import (
    composite_score,
    momentum_score,
    quality_score,
    zscore_cross_sectionally,
)

PriceSeries = dict[str, list[tuple[date, float]]]
FundamentalsInput = dict[str, tuple[float, float, float]]  # roe, debt_to_equity, eps_growth


@dataclass(frozen=True)
class RebalanceSelection:
    date: date
    selected: list[str]
    weights: dict[str, float]
    scores: dict[str, float]
    eligible_count: int
    quality_coverage_count: int


@dataclass(frozen=True)
class PortfolioResult:
    equity_curve: list[tuple[date, float]] = field(default_factory=list)
    rebalances: list[RebalanceSelection] = field(default_factory=list)
    final_equity: float = 0.0


def index_on_or_before(dates: list[date], target: date) -> Optional[int]:
    """Index of the latest entry in a sorted `dates` list that's <= target,
    or None if every entry is after target."""
    i = bisect.bisect_right(dates, target) - 1
    return i if i >= 0 else None


def _trailing_return(closes: list[float], as_of_index: int, lookback_days: int) -> Optional[float]:
    start = as_of_index - lookback_days
    if start < 0:
        return None
    if closes[start] == 0:
        return None
    return closes[as_of_index] / closes[start] - 1


def _annualized_vol(closes: list[float], as_of_index: int, window_days: int) -> Optional[float]:
    start = as_of_index - window_days
    if start < 0:
        return None
    window = closes[start : as_of_index + 1]
    daily_returns = [
        window[i] / window[i - 1] - 1 for i in range(1, len(window)) if window[i - 1] != 0
    ]
    if len(daily_returns) < 2:
        return None
    mean = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean) ** 2 for r in daily_returns) / len(daily_returns)
    return math.sqrt(variance) * math.sqrt(252)


def _simple_moving_average(closes: list[float], as_of_index: int, window_days: int) -> Optional[float]:
    start = as_of_index - window_days + 1
    if start < 0:
        return None
    window = closes[start : as_of_index + 1]
    return sum(window) / len(window)


def run_portfolio_backtest(
    price_series: PriceSeries,
    fundamentals: FundamentalsInput,
    rebalance_dates: list[date],
    top_n: int,
    initial_capital: float = 100.0,
    use_quality: bool = True,
    trend_filter: bool = False,
    lookback_days_6m: int = 126,
    lookback_days_12m: int = 252,
    trend_sma_days: int = 200,
    quality_weight: float = 0.5,
) -> PortfolioResult:
    """`price_series` and `fundamentals` cover the full backtest universe --
    a stock missing from `fundamentals` (no usable ROE/debt-equity/EPS
    growth) is still eligible on momentum alone (see
    scoring.composite_score), reported via `quality_coverage_count`.

    The trading calendar is derived as the union of every date seen across
    `price_series`, sorted -- callers don't need to supply one separately.
    A stock missing a bar on a given calendar day (e.g. a trading halt)
    carries forward its last known close for mark-to-market rather than
    being dropped or crashing the run.
    """
    calendar = sorted({d for bars in price_series.values() for d, _ in bars})
    dates_by_symbol = {sym: [d for d, _ in bars] for sym, bars in price_series.items()}
    closes_by_symbol = {sym: [c for _, c in bars] for sym, bars in price_series.items()}

    rebalances: list[RebalanceSelection] = []
    # symbol -> units held (constant between rebalances; recomputed at each one)
    units: dict[str, float] = {}
    last_known_price: dict[str, float] = {}
    equity_curve: list[tuple[date, float]] = []
    portfolio_equity = initial_capital

    # Snap every requested rebalance date to the nearest real trading day
    # on/before it -- a date that isn't itself a trading day for anyone
    # (e.g. a semiannual calendar boundary that falls on a weekend/holiday)
    # must still trigger a rebalance, not be silently skipped entirely.
    sorted_rebalance_dates = sorted(rebalance_dates)
    snapped_days = sorted(
        {
            calendar[idx]
            for d in sorted_rebalance_dates
            if (idx := index_on_or_before(calendar, d)) is not None
        }
    )
    if not snapped_days:
        return PortfolioResult(equity_curve=[], rebalances=[], final_equity=initial_capital)
    start_idx = calendar.index(snapped_days[0])
    rebalance_set = set(snapped_days)

    for day in calendar[start_idx:]:
        if day in rebalance_set:
            if units:
                # Crystallize the outgoing holdings' value at today's
                # prices before re-selecting -- if nothing was held (either
                # the very start of the backtest, or the previous rebalance
                # selected nothing), portfolio_equity simply carries
                # forward unchanged: holding zero stocks means holding cash
                # at its last known value, never $0.
                portfolio_equity = _mark_to_market(
                    units, last_known_price, closes_by_symbol, dates_by_symbol, day
                )
            selection = _select(
                day=day,
                dates_by_symbol=dates_by_symbol,
                closes_by_symbol=closes_by_symbol,
                fundamentals=fundamentals,
                top_n=top_n,
                use_quality=use_quality,
                trend_filter=trend_filter,
                lookback_days_6m=lookback_days_6m,
                lookback_days_12m=lookback_days_12m,
                trend_sma_days=trend_sma_days,
                quality_weight=quality_weight,
            )
            rebalances.append(selection)
            units = {}
            for symbol, weight in selection.weights.items():
                idx = index_on_or_before(dates_by_symbol[symbol], day)
                price = closes_by_symbol[symbol][idx]
                units[symbol] = (weight * portfolio_equity) / price

        if units:
            portfolio_equity = _mark_to_market(
                units, last_known_price, closes_by_symbol, dates_by_symbol, day
            )
        equity_curve.append((day, portfolio_equity))

    final_equity = equity_curve[-1][1] if equity_curve else initial_capital
    return PortfolioResult(equity_curve=equity_curve, rebalances=rebalances, final_equity=final_equity)


def _mark_to_market(
    units: dict[str, float],
    last_known_price: dict[str, float],
    closes_by_symbol: dict[str, list[float]],
    dates_by_symbol: dict[str, list[date]],
    day: date,
) -> float:
    total = 0.0
    for symbol, qty in units.items():
        idx = index_on_or_before(dates_by_symbol[symbol], day)
        price = closes_by_symbol[symbol][idx] if idx is not None else last_known_price.get(symbol)
        if price is not None:
            last_known_price[symbol] = price
        else:
            price = 0.0
        total += qty * price
    return total


def _select(
    day: date,
    dates_by_symbol: dict[str, list[date]],
    closes_by_symbol: dict[str, list[float]],
    fundamentals: FundamentalsInput,
    top_n: int,
    use_quality: bool,
    trend_filter: bool,
    lookback_days_6m: int,
    lookback_days_12m: int,
    trend_sma_days: int,
    quality_weight: float,
) -> RebalanceSelection:
    eligible_symbols: list[str] = []
    raw_momentum: dict[str, float] = {}

    for symbol, dates in dates_by_symbol.items():
        idx = index_on_or_before(dates, day)
        if idx is None:
            continue
        closes = closes_by_symbol[symbol]
        r6m = _trailing_return(closes, idx, lookback_days_6m)
        r12m = _trailing_return(closes, idx, lookback_days_12m)
        vol = _annualized_vol(closes, idx, lookback_days_12m)
        if r6m is None or r12m is None or vol is None:
            continue
        score = momentum_score(r6m, r12m, vol)
        if score is None:
            continue
        if trend_filter:
            sma = _simple_moving_average(closes, idx, trend_sma_days)
            if sma is None or closes[idx] < sma:
                continue
        eligible_symbols.append(symbol)
        raw_momentum[symbol] = score

    momentum_z = dict(
        zip(eligible_symbols, zscore_cross_sectionally([raw_momentum[s] for s in eligible_symbols]))
    )

    quality_raw: dict[str, float] = {}
    if use_quality:
        for symbol in eligible_symbols:
            fdata = fundamentals.get(symbol)
            if fdata is None:
                continue
            roe, debt_to_equity, eps_growth = fdata
            quality_raw[symbol] = quality_score(roe, debt_to_equity, eps_growth)
    quality_symbols = list(quality_raw.keys())
    quality_z = dict(
        zip(quality_symbols, zscore_cross_sectionally([quality_raw[s] for s in quality_symbols]))
    )

    composite: dict[str, float] = {
        symbol: composite_score(
            momentum_z[symbol], quality_z.get(symbol), quality_weight=quality_weight
        )
        for symbol in eligible_symbols
    }

    ranked = sorted(composite.items(), key=lambda kv: kv[1], reverse=True)
    selected = [symbol for symbol, _ in ranked[:top_n]]
    weight = 1.0 / len(selected) if selected else 0.0
    weights = {symbol: weight for symbol in selected}

    return RebalanceSelection(
        date=day,
        selected=selected,
        weights=weights,
        scores=composite,
        eligible_count=len(eligible_symbols),
        quality_coverage_count=len(quality_symbols),
    )


__all__ = [
    "run_portfolio_backtest",
    "PortfolioResult",
    "RebalanceSelection",
    "index_on_or_before",
]
