"""Black-Scholes helpers, for strategy D only.

D is the one strategy permitted to choose its own strike: it writes where the
premium is genuinely rich, meaning the strike whose IMPLIED volatility stands
furthest above the stock's own REALISED volatility. That is the variance risk
premium, which is the documented source of edge in option selling and -- more
usefully for a per-stock ranking -- a persistent characteristic of a stock
rather than a realised outcome, so it has a chance of ranking stably.

A/B/C/E/F never price anything. They use a fixed 5% OTM rule, which is what
keeps their matched-pair comparisons attributable to one decision.

Stdlib only, matching `growmore_bot.backtest.deflated_sharpe`, so every number
is hand-checkable in a test.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

#: Indian risk-free proxy over the study window. The result is insensitive to
#: it -- a monthly option's discount factor moves the price by basis points --
#: but it is stated rather than left at zero.
RISK_FREE = 0.065

TRADING_DAYS = 252
_MIN_VOL, _MAX_VOL = 1e-4, 5.0


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(spot: float, strike: float, years: float, vol: float, opt_type: str) -> float:
    """Black-Scholes European price. Stock options on NSE are European."""
    if years <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        intrinsic = (spot - strike) if opt_type == "CE" else (strike - spot)
        return max(intrinsic, 0.0)
    d1 = (math.log(spot / strike) + (RISK_FREE + 0.5 * vol * vol) * years) / (
        vol * math.sqrt(years)
    )
    d2 = d1 - vol * math.sqrt(years)
    discount = math.exp(-RISK_FREE * years)
    if opt_type == "CE":
        return spot * _norm_cdf(d1) - strike * discount * _norm_cdf(d2)
    return strike * discount * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def option_delta(spot: float, strike: float, years: float, vol: float, opt_type: str) -> float:
    """dPrice/dSpot. Used as the risk cap on D's strike search."""
    if years <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + (RISK_FREE + 0.5 * vol * vol) * years) / (
        vol * math.sqrt(years)
    )
    return _norm_cdf(d1) if opt_type == "CE" else _norm_cdf(d1) - 1.0


def implied_vol(
    price: float, spot: float, strike: float, years: float, opt_type: str
) -> Optional[float]:
    """Back out volatility by bisection, or None if the price cannot imply one.

    Refuses rather than guesses in three cases, all of which occur in real
    settlement data: a non-positive price, an expired contract, and a quote
    below intrinsic value. Inventing a volatility from an arbitrage-violating
    print would put a fabricated number straight into D's strike ranking.
    """
    if price <= 0 or years <= 0 or spot <= 0 or strike <= 0:
        return None
    intrinsic = max((spot - strike) if opt_type == "CE" else (strike - spot), 0.0)
    if price < intrinsic - 1e-9:
        return None
    if bs_price(spot, strike, years, _MAX_VOL, opt_type) < price:
        return None

    low, high = _MIN_VOL, _MAX_VOL
    for _ in range(100):
        mid = 0.5 * (low + high)
        if bs_price(spot, strike, years, mid, opt_type) < price:
            low = mid
        else:
            high = mid
        if high - low < 1e-6:
            break
    return 0.5 * (low + high)


def realised_vol(closes: Sequence[float], periods_per_year: int = TRADING_DAYS) -> float:
    """Annualised stdev of log returns. Population stdev, matching metrics.py."""
    usable = [c for c in closes if c and c > 0]
    if len(usable) < 3:
        return 0.0
    rets = [math.log(b / a) for a, b in zip(usable, usable[1:])]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * math.sqrt(periods_per_year)


__all__ = ["RISK_FREE", "bs_price", "option_delta", "implied_vol", "realised_vol"]
