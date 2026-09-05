"""Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014).

Why this is here. `run_all` now sweeps 24 strategy variants across 8
instruments -- 192 backtests -- and the results doc reports the best of them.
The maximum Sharpe of N trials is inflated even when every candidate is pure
noise, so "Sharpe 1.68, the best of 192" is not the same claim as "Sharpe
1.68, hypothesised once and tested once", and reporting them the same way
overstates what has been shown. The DSR gives the probability that an
observed Sharpe reflects real skill once you account for (a) how many things
you tried and (b) the non-normality of the return series, which for a
trend-following equity curve is always negative skew and fat tails.

    SR0 = sqrt(Var(SR)) * [ (1-g)*Z(1 - 1/N) + g*Z(1 - 1/(N*e)) ]

        the Sharpe you'd EXPECT the luckiest of N independent trials to post
        by chance alone. g is Euler-Mascheroni; the two-term form is the
        standard extreme-value approximation for the expected maximum of N
        draws.

    DSR = Phi( (SR - SR0) * sqrt(T-1) / sqrt(1 - skew*SR + (kurt-1)/4 * SR^2) )

All quantities are PER-OBSERVATION, never annualised -- mixing the two is the
easiest way to get a meaningless answer, so `deflated_sharpe_ratio` refuses
an implausibly large `observed_sharpe` rather than returning nonsense. Use
`annualised_to_per_observation` to convert.

Stdlib only, matching the rest of `growmore_bot`: Phi comes from math.erfc
and its inverse from Acklam's rational approximation, so no scipy dependency
is introduced for one function.
"""
from __future__ import annotations

import math

EULER_MASCHERONI = 0.5772156649015329

#: A per-observation Sharpe above this is almost certainly an annualised
#: figure passed by mistake -- 0.5 per day is ~7.9 annualised.
_IMPLAUSIBLE_PER_OBSERVATION_SHARPE = 0.5

# Acklam's rational approximation to the inverse normal CDF (|error| < 1.15e-9).
_A = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
      1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)
_B = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
      6.680131188771972e01, -1.328068155288572e01)
_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
      -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)
_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
      3.754408661907416e00)
_P_LOW = 0.02425


def norm_cdf(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def norm_ppf(p: float) -> float:
    """Standard normal inverse CDF (quantile function)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be strictly between 0 and 1")
    if p < _P_LOW:
        q = math.sqrt(-2 * math.log(p))
        return (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / (
            (((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1
        )
    if p > 1 - _P_LOW:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / (
            (((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1
        )
    q = p - 0.5
    r = q * q
    return (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q / (
        ((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1
    )


def annualised_to_per_observation(sharpe: float, periods_per_year: int = 252) -> float:
    """Undo the sqrt(periods) annualisation `backtest.metrics.sharpe_ratio`
    applies, since every formula here works per observation."""
    return sharpe / math.sqrt(periods_per_year)


def expected_max_sharpe(sharpe_variance: float, n_trials: int) -> float:
    """The Sharpe the LUCKIEST of `n_trials` independent trials would be
    expected to post by chance alone, given how widely Sharpe varies across
    those trials. This is the bar a selected result has to clear before it
    means anything.

    `n_trials` should be the EFFECTIVE number of independent trials, not the
    raw backtest count -- variants within a strategy family and instruments
    within a correlated block are far from independent, so 192 runs is
    nowhere near 192 trials.
    """
    if sharpe_variance < 0:
        raise ValueError("sharpe_variance must not be negative")
    if n_trials < 1:
        raise ValueError("n_trials must be at least 1")
    if n_trials == 1:
        return 0.0
    g = EULER_MASCHERONI
    term = (1 - g) * norm_ppf(1 - 1.0 / n_trials) + g * norm_ppf(1 - 1.0 / (n_trials * math.e))
    return math.sqrt(sharpe_variance) * term


def deflated_sharpe_ratio(
    observed_sharpe: float,
    sharpe_variance: float,
    n_trials: int,
    n_observations: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Probability that `observed_sharpe` reflects real skill rather than the
    luckiest draw from `n_trials`. Above ~0.95 is the conventional bar.

    All Sharpe quantities are PER OBSERVATION (see
    `annualised_to_per_observation`). `kurtosis` is the raw fourth moment --
    3.0 for a normal distribution, not excess kurtosis.
    """
    if n_observations < 2:
        raise ValueError("n_observations must be at least 2")
    if abs(observed_sharpe) > _IMPLAUSIBLE_PER_OBSERVATION_SHARPE:
        raise ValueError(
            f"observed_sharpe={observed_sharpe} looks annualised, not per-observation "
            "(a per-observation Sharpe above 0.5 is ~7.9 annualised). Convert it with "
            "annualised_to_per_observation() first -- mixing the two silently produces "
            "a meaningless answer."
        )
    bar = expected_max_sharpe(sharpe_variance, n_trials)
    denominator_sq = (
        1.0 - skew * observed_sharpe + (kurtosis - 1.0) / 4.0 * observed_sharpe**2
    )
    if denominator_sq <= 0:
        # Degenerate moments; no meaningful statement can be made.
        return 0.0
    z = (observed_sharpe - bar) * math.sqrt(n_observations - 1) / math.sqrt(denominator_sq)
    return norm_cdf(z)


__all__ = [
    "norm_cdf",
    "norm_ppf",
    "annualised_to_per_observation",
    "expected_max_sharpe",
    "deflated_sharpe_ratio",
]
