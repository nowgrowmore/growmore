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
underlying futures series) for a strike whose IV is missing -- the same
per-strike (not chain-wide) fallback discipline the research module
documents, so one illiquid strike never throws away every other strike's
real market-implied delta. An IV that is PRESENT but implausible is treated
identically to a missing one -- see `MIN_PLAUSIBLE_IV`/`MAX_PLAUSIBLE_IV`
and the real 259.33% production print that made that necessary.

**Executability gate**: confirmed 2026-09-14 against a REAL production pick
-- Dhan's `last_price` can be a stale/phantom number that has nothing to do
with what's actually tradeable. A live SILVERM PE chain quoted strike 207000
at `last_price=27409.5` while its own real market was `top_bid_price=38` /
`top_ask_price=2044.5` (oi=0, volume=0, implied_volatility=259.33%) -- an
order at that "price" could never fill. The pre-existing `min_open_interest`
floor does NOT reliably catch this: it's a *complementary* filter, not a
substitute. Confirmed separately: a strike with oi=1 (nonzero, clearing any
sane OI floor) can still show `last_price` sitting BELOW `top_bid_price`.
So, in addition to the OI floor, every candidate row must also have a
verifiable, executable `ltp`: `top_bid_price` and `top_ask_price` must both
be present (not None -- a row Dhan gives no two-sided market for fails
closed, not open) and strictly positive, and `ltp` must fall within
`[top_bid_price, top_ask_price]` inclusive of the boundary. A row failing
this check is excluded from `evaluate_candidates` (and therefore from
`select_strike_by_target_delta`) exactly like a row failing the OI floor --
including one whose (garbage) delta would otherwise have made it the
closest match to the target delta.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from growmore_bot.broker.dhan_client import OptionChainRow, OptionChainSnapshot
from growmore_bot.mcx_options.pricing import black76_delta

#: The band a quoted `OptionChainRow.iv` must fall inside to be trusted as a
#: real market-implied vol. Confirmed 2026-09-14 against a REAL production
#: row: Dhan quoted `implied_volatility=259.33%` for a SILVERM PE (the same
#: phantom row the executability gate was added for). Feeding sigma=2.59 into
#: Black-76 flattens every strike's delta toward 0.5, which makes the whole
#: target-delta pick meaningless -- the picker would then be choosing on
#: noise while looking like it was choosing on delta. The bounds are
#: deliberately WIDE (2% to 150% annualised): they are a garbage filter, not
#: a view on what gold/silver vol "should" be -- a genuine 80% vol print
#: during a bullion shock must still be used verbatim.
MIN_PLAUSIBLE_IV = 0.02
MAX_PLAUSIBLE_IV = 1.50


def _usable_iv(row_iv: Optional[float], sigma: float) -> float:
    """The vol to price this row with: its own quoted IV when that IV is both
    present and plausible, else the caller-supplied `sigma`.

    An implausible IV is treated exactly like a MISSING one -- fall back,
    never drop the row (that would throw away a perfectly tradeable strike
    over a bad vol print) and never trust the number. See
    `MIN_PLAUSIBLE_IV`/`MAX_PLAUSIBLE_IV` above for the confirmed production
    incident this guards.
    """
    if row_iv is None:
        return sigma
    if not (MIN_PLAUSIBLE_IV <= row_iv <= MAX_PLAUSIBLE_IV):
        return sigma
    return row_iv


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
    #: Carried through for dashboard transparency (so a future UI could show
    #: the spread `ltp` was verified against) -- None when the underlying
    #: row's field was None.
    top_bid_price: Optional[float] = None
    top_ask_price: Optional[float] = None


def _is_executable(row: OptionChainRow) -> bool:
    """True iff `row.ltp` is a real, currently-tradeable price -- i.e. Dhan
    quotes a two-sided market for this row and `ltp` falls within it
    (inclusive of the boundary prices themselves).

    See module docstring's "Executability gate" section for why this exists
    and why the OI floor alone is not sufficient.
    """
    bid, ask = row.top_bid_price, row.top_ask_price
    if bid is None or ask is None:
        return False
    if bid <= 0 or ask <= 0:
        return False
    return bid <= row.ltp <= ask


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

    A row must ALSO pass the executability gate (see module docstring) to
    survive -- an OI-clearing row whose `ltp` isn't a real tradeable price is
    excluded just like a thin-OI row.
    """
    survivors = [
        row
        for row in chain.rows
        if row.opt_type == opt_type and row.oi >= min_open_interest and _is_executable(row)
    ]
    evaluations = []
    for row in survivors:
        vol = _usable_iv(row.iv, sigma)
        delta = black76_delta(opt_type, futures_price, row.strike, T_years, vol, r)
        evaluations.append(
            CandidateEvaluation(
                strike=row.strike, delta=delta, oi=row.oi, ltp=row.ltp,
                top_bid_price=row.top_bid_price, top_ask_price=row.top_ask_price,
            )
        )
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
    `target_delta`. Rows of the other `opt_type`, with `oi < min_open_interest`,
    or that fail the executability gate (see module docstring -- `ltp` not
    verifiably within the row's own bid-ask market), are excluded before
    ranking -- including a row whose (garbage) delta would otherwise have
    been the closest match. `None` when nothing survives (empty chain, no
    matching side, or every row filtered out).

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


__all__ = [
    "CandidateEvaluation",
    "MIN_PLAUSIBLE_IV",
    "MAX_PLAUSIBLE_IV",
    "evaluate_candidates",
    "select_strike_by_target_delta",
]
