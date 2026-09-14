"""Target-delta strike selection for the LIVE MCX Goldmini/Silvermini
options-selling engine, over a live `OptionChainSnapshot`
(growmore_bot.broker.dhan_client).

Live analog of `research/mcx_options/strike_selection.py` -- same contract
(rank candidates by |delta - target_delta| after an OI floor, Black-76
delta), freshly written against this repo's own `OptionChainRow`/
`OptionChainSnapshot` dataclasses rather than the offline backtest's
`MCXOptionRow` (bhavcopy-shaped).

**IV, live vs. backtest**: Dhan's real-time option chain already quotes each
strike's own implied vol (`OptionChainRow.iv`, a fraction, None when Dhan
has no quote for that strike) -- there is no raw settlement price to bisect
an IV out of the way `research/mcx_options/strike_selection.py` must (MCX
bhavcopy rows carry settlement prices, not IV). This module therefore uses
`row.iv` directly when present, and falls back to the caller-supplied
`sigma` (expected to be something like a realised-vol estimate from the
underlying futures series) ONLY for a strike whose IV is missing -- the same
per-strike (not chain-wide) fallback discipline the research module
documents, so one illiquid strike never throws away every other strike's
real market-implied delta.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from growmore_bot.broker.dhan_client import OptionChainRow, OptionChainSnapshot
from growmore_bot.mcx_options.pricing import black76_delta


@dataclass(frozen=True)
class CandidateEvaluation:
    """One OI-surviving chain row's computed Black-76 delta, alongside the
    raw fields a dashboard needs to show "what was evaluated" without
    re-deriving anything (see `evaluate_candidates`).
    """

    strike: float
    delta: float
    oi: float
    ltp: float


def evaluate_candidates(
    chain: OptionChainSnapshot,
    opt_type: str,
    futures_price: float,
    T_years: float,
    sigma: float,
    r: float,
    min_open_interest: int,
) -> list[CandidateEvaluation]:
    """Every `opt_type` row in `chain` that clears the `min_open_interest`
    floor, with its Black-76 delta computed (using the row's own quoted IV
    when present, else the caller-supplied `sigma` -- see module docstring's
    per-strike fallback discipline), sorted by strike ascending.

    Deliberately does NOT rank by closeness to any target delta -- "closest
    to target" is a presentation/selection concern for the caller (see
    `select_strike_by_target_delta`), not an evaluation concern. This lets a
    caller (or the dashboard) show the full evaluated set, not just the
    eventual winner.
    """
    survivors = [
        row for row in chain.rows if row.opt_type == opt_type and row.oi >= min_open_interest
    ]
    evaluations = []
    for row in survivors:
        vol = row.iv if row.iv is not None else sigma
        delta = black76_delta(opt_type, futures_price, row.strike, T_years, vol, r)
        evaluations.append(CandidateEvaluation(strike=row.strike, delta=delta, oi=row.oi, ltp=row.ltp))
    evaluations.sort(key=lambda c: c.strike)
    return evaluations


def select_strike_by_target_delta(
    chain: OptionChainSnapshot,
    opt_type: str,
    futures_price: float,
    T_years: float,
    sigma: float,
    r: float,
    target_delta: float,
    min_open_interest: int,
) -> Optional[OptionChainRow]:
    """The chain row (of `opt_type`) whose |delta| is closest to
    `target_delta`. Rows of the other `opt_type`, or with
    `oi < min_open_interest`, are excluded before ranking. `None` when
    nothing survives (empty chain, no matching side, or every row filtered
    out by the OI floor).

    Internally calls `evaluate_candidates` for the delta math, then picks the
    entry with the smallest `abs(abs(delta) - target_delta)`, so the two
    functions never compute delta independently/inconsistently.
    """
    candidates = evaluate_candidates(
        chain, opt_type, futures_price, T_years, sigma, r, min_open_interest
    )
    if not candidates:
        return None

    best = min(candidates, key=lambda c: abs(abs(c.delta) - target_delta))

    by_strike = {row.strike: row for row in chain.rows if row.opt_type == opt_type}
    return by_strike[best.strike]


__all__ = ["CandidateEvaluation", "evaluate_candidates", "select_strike_by_target_delta"]
