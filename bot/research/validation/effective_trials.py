"""Estimating how many INDEPENDENT trials a backtest sweep really represents.

This is the input the Deflated Sharpe Ratio is most sensitive to, and the
easiest to get badly wrong. The raw run count is not it: parameter variants
within a strategy family are near-copies of one another, and instruments
within a correlated block (two precious metals, four base metals) move
together. 192 backtests are nowhere near 192 independent bets.

Two estimators, deliberately different in character:

  * the participation ratio of the correlation matrix's eigenvalues,
    (sum L)^2 / sum(L^2) -- the standard "how many independent directions are
    in here" measure, which degrades smoothly rather than depending on a
    threshold;
  * a correlation-threshold cluster count at rho = 0.5, which is cruder but
    easy to sanity-check by eye.

Callers should take the SMALLER of the two, since a smaller N sets a lower
selection-luck bar and is therefore the LESS conservative choice for the
strategy being judged -- no; take the smaller because it is the more
conservative estimate of how much independent evidence exists. Either way,
report both so a surprising number is visible rather than buried.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def effective_trials(returns: pd.DataFrame) -> tuple[float, int]:
    """(participation-ratio estimate, correlation-cluster count) for a frame
    of per-run return series (runs as columns, dates as the index).

    Columns with different date coverage are kept, not dropped: instruments
    have genuinely different listing histories, and requiring every series to
    span the full union once threw away nearly an entire sweep and reported
    "1 effective trial", which set the selection bar to zero and declared all
    192 results significant.
    """
    if returns is None or returns.empty:
        return 1.0, 1

    # Keep every series that has any data; a missing day is "this run wasn't
    # running", i.e. flat, which is a zero return rather than an unknown.
    usable = returns.dropna(axis=1, how="all").fillna(0.0)
    # A run that never traded has a flat curve and zero variance; its
    # correlation with anything is undefined and would poison the eigenvalues.
    usable = usable.loc[:, usable.std(ddof=0) > 0]

    n_cols = usable.shape[1]
    if n_cols < 2:
        return float(max(n_cols, 1)), max(n_cols, 1)

    corr = np.nan_to_num(usable.corr().to_numpy(), nan=0.0)
    eigenvalues = np.clip(np.linalg.eigvalsh(corr), 0.0, None)
    total = eigenvalues.sum()
    participation = (
        float(total**2 / np.square(eigenvalues).sum()) if total > 0 else float(n_cols)
    )

    # Greedy grouping: each unclaimed series seeds a cluster and absorbs
    # everything correlated with it at rho >= 0.5.
    unassigned = set(range(n_cols))
    clusters = 0
    while unassigned:
        seed = unassigned.pop()
        clusters += 1
        for other in list(unassigned):
            if corr[seed, other] >= 0.5:
                unassigned.discard(other)

    return participation, clusters


__all__ = ["effective_trials"]
