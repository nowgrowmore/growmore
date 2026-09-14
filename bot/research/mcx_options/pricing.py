"""Black-76 option pricing for MCX commodity options.

MCX commodity options are options on the underlying FUTURES contract, not on
spot -- unlike NSE stock options (Black-Scholes, spot-driven; see
`research/stock_options/pricing.py`, which this module mirrors in style:
stdlib-only, bisection for implied vol, hand-checkable numbers).

Black-76:
    d1 = (ln(F/K) + 0.5*sigma^2*T) / (sigma*sqrt(T))
    d2 = d1 - sigma*sqrt(T)
    call = discount * (F*N(d1) - K*N(d2))
    put  = discount * (K*N(-d2) - F*N(-d1))
    call delta = discount * N(d1)
    put delta  = discount * (N(d1) - 1)
where discount = exp(-r*T), F = futures price, K = strike, T = years to
expiry, sigma = volatility, r = risk-free rate, N = standard normal CDF.
"""
from __future__ import annotations

import math
from typing import Optional

from research.stock_options.pricing import realised_vol

_MIN_VOL, _MAX_VOL = 1e-4, 5.0


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


__all__ = [
    "black76_price",
    "black76_delta",
    "implied_vol_b76",
    "realised_vol",
]
