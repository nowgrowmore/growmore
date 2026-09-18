"""Black-76 option pricing for the LIVE MCX Goldmini/Silvermini options-
selling engine.

Ported from `research/mcx_options/pricing.py` (same math, freshly written --
see `growmore_bot/wheel_basket/universe.py`'s own comment on the one-
directional research -> growmore_bot convention this repo follows: never the
reverse). MCX commodity options are options on the underlying FUTURES
contract, not spot.

Black-76:
    d1 = (ln(F/K) + 0.5*sigma^2*T) / (sigma*sqrt(T))
    d2 = d1 - sigma*sqrt(T)
    call = discount * (F*N(d1) - K*N(d2))
    put  = discount * (K*N(-d2) - F*N(-d1))
    call delta = discount * N(d1)
    put delta  = discount * (N(d1) - 1)
where discount = exp(-r*T), F = futures price, K = strike, T = years to
expiry, sigma = volatility, r = risk-free rate, N = standard normal CDF.

Stdlib only -- no dependency on `research.*` or on pandas/numpy, matching
`growmore_bot/indicators.py`'s dependency-light philosophy.
"""
from __future__ import annotations

import math
from typing import Any, Optional, Sequence

_MIN_VOL, _MAX_VOL = 1e-4, 5.0


#: Trading days in an MCX year, for annualising a realised-vol estimate.
#: 252 is the repo-wide convention (`growmore_bot/indicators.py`'s
#: `periods_per_year` default, `research/stock_options/pricing.py`'s
#: TRADING_DAYS). Note this is deliberately NOT the 365.25 used for an
#: option's time-to-expiry: `T_years` measures CALENDAR decay, while vol is
#: annualised over the days the market actually printed a return.
MCX_TRADING_DAYS = 252


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _intrinsic(option_type: str, F: float, K: float) -> float:
    return max((F - K) if option_type == "CE" else (K - F), 0.0)


def black76_price(option_type: str, F: float, K: float, T: float, sigma: float, r: float) -> float:
    """Black-76 European price. MCX commodity options are European."""
    if T <= 0 or sigma <= 0 or F <= 0 or K <= 0:
        discount = math.exp(-r * T) if T > 0 else 1.0
        return discount * _intrinsic(option_type, F, K)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    discount = math.exp(-r * T)
    if option_type == "CE":
        return discount * (F * _norm_cdf(d1) - K * _norm_cdf(d2))
    return discount * (K * _norm_cdf(-d2) - F * _norm_cdf(-d1))


def black76_delta(option_type: str, F: float, K: float, T: float, sigma: float, r: float) -> float:
    """dPrice/dF."""
    if T <= 0:
        # Boundary delta at/after expiry: 1/-1 if strictly ITM, else 0.
        if option_type == "CE":
            return 1.0 if F > K else 0.0
        return -1.0 if F < K else 0.0
    if sigma <= 0 or F <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
    discount = math.exp(-r * T)
    if option_type == "CE":
        return discount * _norm_cdf(d1)
    return discount * (_norm_cdf(d1) - 1.0)


def implied_vol_b76(
    option_type: str, price: float, F: float, K: float, T: float, r: float
) -> Optional[float]:
    """Back out volatility by bisection, or None if the price cannot imply one.

    Refuses rather than guesses: a non-positive price, an expired contract,
    a quote below intrinsic value, or a price above what even max vol could
    produce all return None rather than raising or looping forever.
    """
    if price <= 0 or T <= 0 or F <= 0 or K <= 0:
        return None
    discount = math.exp(-r * T)
    intrinsic = discount * _intrinsic(option_type, F, K)
    if price < intrinsic - 1e-9:
        return None
    if black76_price(option_type, F, K, T, _MAX_VOL, r) < price:
        return None

    low, high = _MIN_VOL, _MAX_VOL
    for _ in range(100):
        mid = 0.5 * (low + high)
        if black76_price(option_type, F, K, T, mid, r) < price:
            low = mid
        else:
            high = mid
        if high - low < 1e-6:
            break
    return 0.5 * (low + high)


def realised_vol(closes: Sequence[float], periods_per_year: int = MCX_TRADING_DAYS) -> float:
    """Annualised stdev of log returns -- population stdev, matching every
    other stdev in this project (`backtest/metrics.py`, `regime.py`'s
    Bollinger bands).

    Mirrored from `research/stock_options/pricing.py` rather than imported:
    `growmore_bot` never imports from `research` (the one-directional
    convention documented in `growmore_bot/wheel_basket/universe.py`), and
    that module additionally pulls in pandas, which the live bot does not
    carry.

    Used as the baseline for the variance-risk-premium filter
    (`MCXOptionsConfig.min_iv_minus_realised_vol`): selling options whose
    implied vol exceeds the underlying's realised vol is the actual edge in
    put selling, so "how volatile has this thing really been" is the number
    an implied vol has to beat.

    Returns 0.0 rather than raising when there is too little usable history to
    say anything -- callers treat a 0.0 baseline as "no opinion", which makes
    the VRP filter trivially satisfiable rather than silently blocking every
    entry on a short series.
    """
    usable = [float(c) for c in closes if c and c > 0]
    if len(usable) < 3:
        return 0.0
    rets = [math.log(b / a) for a, b in zip(usable, usable[1:])]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var) * math.sqrt(periods_per_year)


def realised_vol_from_bars(bars: Sequence[Any], periods_per_year: int = MCX_TRADING_DAYS) -> float:
    """`realised_vol` over the `.close` of each bar -- the shape
    `MCXCycleData.futures_bars` already provides.
    """
    return realised_vol([bar.close for bar in bars], periods_per_year)


__all__ = [
    "black76_price",
    "black76_delta",
    "implied_vol_b76",
    "realised_vol",
    "realised_vol_from_bars",
    "MCX_TRADING_DAYS",
]
