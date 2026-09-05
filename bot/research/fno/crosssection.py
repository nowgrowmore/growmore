"""Turn 210 per-stock results into ONE defensible statement about a config.

THE TRAP. 210 stocks x 3 configs is 630 backtests. The luckiest of 630 posts
an annualised Sharpe near 2.5 on noise alone, so the top of a per-stock
leaderboard carries no information whatsoever. Phase 3 already wrote the
rule, on a run a twentieth this size: "The best result anywhere in the run
was COPPER filtered by GOLDM macd5-13-5 at +0.24 Sharpe. With 45
combinations tried, a best-of-45 at +0.24 against a mean of -0.255 is what
noise looks like, and it should not be pursued." (docs/crosstrend-results.md)

THE FIX. The unit of evidence is the CONFIG, not the stock. Each config is
scored against each stock's OWN buy-and-hold over identical bars, and the
210 differences are summarised as one paired comparison. This is exactly
`research/validation/oos_pairs.py`'s discipline generalised across stocks
rather than across ideas: the two members of a pair differ by exactly one
decision -- trade the rule, or hold the stock -- so the difference is
attributable, and no pair is cherry-picked from a ranking. Three configs is
three declared trials, not 630, and that holds ONLY because nothing selects
across stocks.

WHAT IS REPORTED, AND WHY EACH. The mean alone misleads badly here: one
stock that trends for a decade can drag a losing config's mean positive. So
the median and the win rate carry the verdict, the mean is shown beside them
for honesty about the tail, and the IQR shows the spread. The win rate --
what fraction of stocks the config beat their own buy-and-hold on -- is the
single number a reader should look at first.

THE SIGN TEST IS DELIBERATELY THE WEAK ONE. It assumes independent
observations, and Indian equities are ~60-70% correlated to the Nifty, so
210 stocks are nowhere near 210 independent trials. The p-value here is
therefore an OPTIMISTIC bound, and the report is required to say so and to
pair it with the participation-ratio breadth from
`research/validation/effective_trials.py`. A nominal p of 0.001 across
correlated names is not the evidence it looks like.

TIES are excluded rather than counted, the standard treatment: a stock the
config never traded has a delta of exactly zero, and scoring those as wins
would manufacture significance out of inactivity.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, median
from typing import Sequence


@dataclass(frozen=True)
class CrossSection:
    """One config's record against the per-stock benchmark."""

    n: int                  # stocks compared
    effective_n: int        # non-tied comparisons -- the sign test's sample
    wins: int               # stocks where the config beat its own benchmark
    win_rate: float         # wins / n  (NOT wins / effective_n)
    mean_delta: float
    median_delta: float
    q1_delta: float
    q3_delta: float
    #: Two-sided sign-test p on the non-tied differences. An OPTIMISTIC
    #: bound: it assumes the stocks are independent, and they are not.
    sign_test_p: float

    def as_row(self, label: str) -> str:
        return (
            f"{label:<34} {self.n:>4} {self.win_rate * 100:>6.1f}% "
            f"{self.mean_delta:>+8.3f} {self.median_delta:>+8.3f} "
            f"[{self.q1_delta:>+.2f},{self.q3_delta:>+.2f}] {self.sign_test_p:>8.4f}"
        )


HEADER = (
    f"{'config':<34} {'n':>4} {'win%':>7} {'meandS':>8} {'meddS':>8} "
    f"{'IQR':>14} {'signP':>8}"
)


def _quantile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile. stdlib-only, matching metrics.py."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[int(position)]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _binomial_two_sided_p(wins: int, n: int) -> float:
    """Exact two-sided binomial test at p=0.5, stdlib only.

    Doubling the smaller tail is the conventional treatment and is exact at
    p=0.5 because the distribution is symmetric.
    """
    if n <= 0:
        return 1.0
    tail = min(wins, n - wins)
    cumulative = sum(math.comb(n, k) for k in range(tail + 1)) / (2.0 ** n)
    return min(1.0, 2.0 * cumulative)


def summarise_pairs(pairs: Sequence[tuple[str, float, float]]) -> CrossSection:
    """Summarise (symbol, treatment_metric, benchmark_metric) triples.

    The metric is whatever the caller paired on -- Sharpe for the headline,
    but total return and max drawdown are run through this same function,
    because they will NOT agree and the disagreement is the finding.
    """
    if not pairs:
        return CrossSection(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0)

    deltas = [treatment - benchmark for _symbol, treatment, benchmark in pairs]
    non_tied = [d for d in deltas if d != 0.0]
    wins = sum(1 for d in deltas if d > 0)

    return CrossSection(
        n=len(deltas),
        effective_n=len(non_tied),
        wins=wins,
        win_rate=wins / len(deltas),
        mean_delta=mean(deltas),
        median_delta=median(deltas),
        q1_delta=_quantile(deltas, 0.25),
        q3_delta=_quantile(deltas, 0.75),
        sign_test_p=_binomial_two_sided_p(wins, len(non_tied)),
    )


__all__ = ["CrossSection", "HEADER", "summarise_pairs"]
