"""Pure scoring functions for the cross-sectional momentum+quality strategy.

Deliberate simplifications of NSE's own (not fully public) exact
methodology -- documented here rather than pretending to replicate it:

- `momentum_score`: NSE's Nifty200 Momentum 30 / Nifty Smallcap250 Momentum
  Quality 100 both describe their momentum score as "6-month and 12-month
  price return, adjusted for daily price volatility" without publishing the
  exact weighting. This uses an equal-weighted 6m/12m blend divided by
  annualized volatility -- a reasonable, auditable reading of that
  description, not a reproduction of NSE's internal formula.
- `quality_score`: NSE's own quality score blends ROE, debt/equity, and
  5-year EPS-growth-stability. This uses a fixed-weight composite of ROE,
  (negated) debt/equity, and a single EPS growth figure -- not the 5-year
  stability calculation, which needs more history than is reliably
  available per-stock from the fundamentals source used here (see
  fundamentals.py).
- `composite_score`: NSE weights by momentum-quality score AND free-float
  market cap (capped at 3-5%). This is deliberately simpler: an equal-weight
  blend of z-scored momentum and quality, feeding into equal-weight
  portfolio construction in portfolio_engine.py -- which also sidesteps the
  "1 lot regardless of capital" bias flagged in docs/backtest-results.md for
  the commodity sweep.
"""
from __future__ import annotations

import math
from typing import Optional


def momentum_score(
    returns_6m: float, returns_12m: float, annualized_vol: float
) -> Optional[float]:
    """None (not a divide-by-zero crash) when volatility is exactly 0 -- a
    real edge case only for a stock with no price movement over the window
    (e.g. suspended trading), where momentum isn't a meaningful concept.
    """
    if annualized_vol == 0:
        return None
    return (0.5 * returns_6m + 0.5 * returns_12m) / annualized_vol


def quality_score(roe: float, debt_to_equity: float, eps_growth: float) -> float:
    """Higher is better throughout: high ROE, low debt/equity, high EPS
    growth. Fixed weights (0.4 / 0.3 / 0.3) -- a reasonable, documented
    choice, not derived from NSE's own (undisclosed) weighting.
    """
    return 0.4 * roe - 0.3 * debt_to_equity + 0.3 * eps_growth


def zscore_cross_sectionally(values: list[float]) -> list[float]:
    """Standardize a list of raw scores against each other (population
    stdev, not sample) so momentum and quality -- measured in unrelated
    units -- can be combined. All-identical input (including a single
    value, or an empty list) can't be meaningfully standardized -- returns
    zeros rather than dividing by zero.
    """
    if not values:
        return []
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    std = math.sqrt(variance)
    if std == 0:
        return [0.0] * n
    return [(v - mean) / std for v in values]


def composite_score(
    momentum_z: float, quality_z: Optional[float], quality_weight: float = 0.5
) -> float:
    """Blend of z-scored momentum and quality. `quality_z=None` (no usable
    fundamentals for this stock -- see fundamentals.py) falls back to
    momentum alone rather than excluding the stock outright; portfolio
    construction still reports fundamentals coverage separately.
    """
    if quality_z is None:
        return momentum_z
    return (1 - quality_weight) * momentum_z + quality_weight * quality_z


__all__ = [
    "momentum_score",
    "quality_score",
    "zscore_cross_sectionally",
    "composite_score",
]
