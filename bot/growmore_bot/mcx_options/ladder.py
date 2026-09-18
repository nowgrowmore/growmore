"""Choosing the WEEK'S batch of new short puts for the MCX options strategy.

`strike_selection.py` answers "which single strike in this one chain is
closest to a target delta". This module answers the question the weekly
ladder actually poses: *given several expiries' chains and the puts I already
hold, which `count` new (expiry, strike) pairs should I write, and why did
nothing else qualify?*

**Why a separate module.** `strike_selection.evaluate_candidates`' own
docstring says ranking is a caller concern, deliberately, "so a caller (or
the dashboard) can show the full evaluated set, not just the eventual
winner". This is that caller. Keeping it separate means the per-chain
primitives (the OI floor, the bid/ask executability gate, the IV plausibility
clamp) stay untouched and shared.

**Filter, then rank -- never a weighted composite.** Every constraint below
is a hard filter with a name and a config column, and the survivors are
ranked on one number. After a losing trade you can point at the filter that
should have caught it; with opaque weights you cannot, and tuning them is
indistinguishable from curve-fitting.

The filters, in the order they are applied:

  1. Out of the money -- a short put at or through the futures price is not
     premium income, it is buying the underlying at a worse price.
  2. The per-chain gates already in `strike_selection.evaluate_candidates`:
     open interest, the bid/ask executability check, and the IV plausibility
     clamp (a real production row once quoted 259.33% implied vol).
  3. `min_volume` -- a strike must have actually printed to be sellable
     (mirrors `growmore_bot/options/strike_selection.py`'s MIN_STRIKE_VOLUME).
  4. `min_credit_pct_of_strike` -- premium richness.
  5. `min_breakeven_cushion_pct` -- how far the market must fall before the
     trade loses money. A more honest risk read than delta alone, because it
     accounts for the premium actually received.
  6. `min_iv_minus_realised_vol` -- the variance risk premium. Selling
     options whose implied vol is below the underlying's realised vol is
     selling insurance too cheaply; this is the only filter here that speaks
     to whether there is an edge at all rather than to risk control.
  7. `max_positions_per_expiry` -- the brake that addresses the real hazard
     of a weekly ladder: without it, a month of rounds stacks every put on
     one expiry date and they all assign on the same morning.
  8. `min_strike_separation_pct` -- against ALREADY-OPEN puts as well as
     against the other picks in this same batch. The second half matters
     more: puts accumulate week on week, and a new put landing on top of an
     existing one is concentration wearing a ladder's clothes.

Survivors are ranked by **annualised return on margin** -- the "CAGR" of a
put sale. This finally gives `MCXOptionsConfig.margin_multiple_of_premium` a
purpose; the 2026-09-15 review found nothing in the codebase read it.

**A short batch is a correct outcome.** When fewer than `count` candidates
survive, this returns fewer picks plus the reasons, and never relaxes a
filter to reach the target. The caller records those reasons on the
selection log, which is how "why didn't it sell two this week" gets
answered.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Optional, Sequence

from growmore_bot.broker.dhan_client import OptionChainRow, OptionChainSnapshot
from growmore_bot.mcx_options.strike_selection import _usable_iv, evaluate_candidates


@dataclass(frozen=True)
class LadderPick:
    """One (expiry, strike) the weekly round decided to write, with the
    numbers that decided it -- carried through so the selection log can show
    WHY this contract, not just which.
    """

    expiry: date
    strike: float
    #: The premium the credit is booked at -- the bid when
    #: `use_bid_for_entry_premium` is set, else the last-traded price.
    premium: float
    delta: float
    annualized_yield: float
    breakeven_cushion: float
    oi: float
    #: The chain row itself, so the caller can write the leg without
    #: re-finding it.
    row: OptionChainRow


def annualized_yield_on_margin(
    premium: float, lots: float, lot_size: int, dte_days: float, margin_multiple: float
) -> float:
    """Annualised return on the margin a short put ties up -- the ranking
    number for the ladder, and the closest honest analogue to "CAGR" for an
    option sale::

        credit = premium * lots * lot_size
        margin = margin_multiple * credit
        yield  = (credit / margin) * (365 / dte)

    Note this reduces to `(1 / margin_multiple) * (365 / dte)` and is
    therefore independent of the premium itself -- which is correct, and the
    point: with margin modelled as a multiple of premium, a bigger credit
    ties up proportionally more margin, so what actually distinguishes two
    candidates is **how fast they turn that margin over**. The premium still
    decides everything else (the credit booked, the breakeven cushion, the
    `min_credit_pct_of_strike` gate).

    Returns 0.0 for a non-positive horizon or margin rather than dividing by
    zero -- a candidate that cannot be scored simply cannot win.
    """
    if dte_days <= 0 or margin_multiple <= 0 or lot_size <= 0 or lots <= 0:
        return 0.0
    credit = premium * lots * lot_size
    if credit <= 0:
        return 0.0
    margin = margin_multiple * credit
    return (credit / margin) * (365.0 / dte_days)


def breakeven_cushion(futures_price: float, strike: float, premium: float) -> float:
    """How far the underlying can fall before this short put starts losing,
    as a fraction of the futures price::

        breakeven = strike - premium
        cushion   = (F - breakeven) / F

    Negative for a strike already through the money net of premium. Unlike
    delta, this credits the premium received, which is what a seller's real
    margin of safety depends on.
    """
    if futures_price <= 0:
        return 0.0
    return (futures_price - (strike - premium)) / futures_price


def _entry_premium(row: OptionChainRow, use_bid: bool) -> float:
    """What the credit would actually be booked at -- the bid when the config
    says so (a seller hits the bid; `ltp` systematically overstates it),
    falling back to `ltp` when Dhan quotes no bid.
    """
    if use_bid and row.top_bid_price:
        return float(row.top_bid_price)
    return row.ltp


def _cfg(config: Any, name: str, default: Any = None) -> Any:
    value = getattr(config, name, None)
    return default if value is None else value


def select_put_ladder(
    chains_by_expiry: Mapping[date, OptionChainSnapshot],
    futures_price: float,
    existing_puts: Sequence[float],
    config: Any,
    target_delta: float,
    lot_size: int,
    today: date,
    realised_vol: float,
    count: int,
    existing_expiry_counts: Optional[Mapping[date, int]] = None,
) -> tuple[list[LadderPick], list[str], list[dict]]:
    """Up to `count` new short puts to write, a human-readable reason for
    every slot that could not be filled, and every candidate evaluated along
    the way.

    The third return value is what the dashboard's "candidates evaluated"
    column renders. It spans ALL the entry expiries, each row tagged with its
    own, so the reader can see the whole board the decision was made from --
    strictly more than the single-chain version this replaced.

    `existing_puts` are the strikes of puts already open for this config (any
    expiry) -- used by the separation filter. `existing_expiry_counts` is how
    many open positions each expiry already carries, for
    `max_positions_per_expiry`; omitted, it is treated as empty.

    Picks are chosen one at a time, each constrained by the ones before it, so
    the batch diversifies against itself as well as against the book.
    """
    if count <= 0:
        return [], [], []

    sigma_fallback = float(_cfg(config, "fallback_sigma", 0.20))
    use_bid = bool(getattr(config, "use_bid_for_entry_premium", False))
    lots = float(_cfg(config, "lots", 1))
    margin_multiple = float(_cfg(config, "margin_multiple_of_premium", 3.0))
    min_volume = float(_cfg(config, "min_volume", 0))
    min_credit_pct = _cfg(config, "min_credit_pct_of_strike")
    min_cushion = _cfg(config, "min_breakeven_cushion_pct")
    min_vrp = _cfg(config, "min_iv_minus_realised_vol")
    separation = _cfg(config, "min_strike_separation_pct")
    max_per_expiry = _cfg(config, "max_positions_per_expiry")
    secondary_delta = _cfg(config, "secondary_target_delta")

    min_separation_abs = float(separation) * futures_price if separation is not None else 0.0
    expiry_counts = dict(existing_expiry_counts or {})

    picks: list[LadderPick] = []
    reasons: list[str] = []
    #: Every (expiry, strike) the OI/executability gate let through, recorded
    #: once regardless of how many slots later reject it -- the transparency
    #: record, not a working set.
    evaluated: dict[tuple[date, float], dict] = {}
    taken_strikes = [float(k) for k in existing_puts]
    #: Contracts already chosen in THIS batch. Tracked separately from the
    #: separation filter, and enforced unconditionally: with
    #: `min_strike_separation_pct` unset, nothing else would stop both slots
    #: landing on the identical (expiry, strike), which is not a ladder --
    #: it is one position written twice.
    taken_contracts: set[tuple[date, float]] = set()

    for slot in range(count):
        # A laddered pair wants its second leg further out of the money, not a
        # near-copy of the first -- see `secondary_target_delta`.
        slot_target = float(secondary_delta) if (slot > 0 and secondary_delta is not None) else target_delta

        best: Optional[LadderPick] = None
        best_key: tuple[float, float] | None = None
        rejected: dict[str, int] = {}

        def _reject(label: str) -> None:
            rejected[label] = rejected.get(label, 0) + 1

        for expiry, chain in sorted(chains_by_expiry.items()):
            dte = (expiry - today).days
            if dte <= 0:
                _reject("expiry not in the future")
                continue
            if max_per_expiry is not None and expiry_counts.get(expiry, 0) >= int(max_per_expiry):
                _reject(f"expiry {expiry} already at its {int(max_per_expiry)}-position cap")
                continue

            T_years = dte / 365.25
            candidates = evaluate_candidates(
                chain,
                opt_type="PE",
                futures_price=futures_price,
                T_years=T_years,
                sigma=sigma_fallback,
                r=0.0,
                min_open_interest=int(_cfg(config, "min_open_interest", 0)),
                max_relative_spread=(
                    float(config.max_relative_spread)
                    if getattr(config, "max_relative_spread", None) is not None
                    else None
                ),
            )
            rows_by_strike = {r.strike: r for r in chain.rows if r.opt_type == "PE"}

            for cand in candidates:
                row = rows_by_strike[cand.strike]
                if (expiry, cand.strike) in taken_contracts:
                    continue  # already taken by an earlier slot in this batch
                if cand.strike >= futures_price:
                    _reject("not out of the money")
                    continue
                # Recorded only once past the structural filters: a strike that
                # is in the money is not a candidate a reader needs to see
                # weighed, it was never eligible. Everything from here on was
                # genuinely in the running.
                evaluated.setdefault(
                    (expiry, cand.strike),
                    {
                        "strike": cand.strike,
                        "delta": cand.delta,
                        "oi": cand.oi,
                        "ltp": cand.ltp,
                        "expiry": expiry.isoformat(),
                    },
                )
                if row.volume < min_volume:
                    _reject("below the volume floor")
                    continue

                premium = _entry_premium(row, use_bid)
                if premium <= 0:
                    _reject("no usable premium")
                    continue
                if min_credit_pct is not None and premium < float(min_credit_pct) * cand.strike:
                    _reject("below the minimum credit")
                    continue

                cushion = breakeven_cushion(futures_price, cand.strike, premium)
                if min_cushion is not None and cushion < float(min_cushion):
                    _reject("below the breakeven cushion floor")
                    continue

                if min_vrp is not None:
                    iv = _usable_iv(row.iv, sigma_fallback)
                    if (iv - realised_vol) < float(min_vrp):
                        _reject("implied vol too close to realised vol")
                        continue

                if min_separation_abs > 0 and any(
                    abs(cand.strike - k) < min_separation_abs for k in taken_strikes
                ):
                    _reject("too close to a strike already held or picked")
                    continue

                pick = LadderPick(
                    expiry=expiry,
                    strike=cand.strike,
                    premium=premium,
                    delta=cand.delta,
                    annualized_yield=annualized_yield_on_margin(
                        premium, lots, lot_size, dte, margin_multiple
                    ),
                    breakeven_cushion=cushion,
                    oi=cand.oi,
                    row=row,
                )
                # Closest to this slot's target delta first -- that is the
                # risk the strategy actually intends to take -- then the
                # faster margin turnover. Delta is bucketed to 0.01 so two
                # candidates at effectively the same risk are separated by
                # yield rather than by a third decimal place of delta.
                key = (round(abs(abs(pick.delta) - slot_target), 2), -pick.annualized_yield)
                if best_key is None or key < best_key:
                    best, best_key = pick, key

        if best is None:
            detail = ", ".join(f"{label} ({n})" for label, n in sorted(rejected.items()))
            reasons.append(
                f"slot {slot + 1} of {count}: no strike qualified"
                + (f" -- rejected: {detail}" if detail else "")
            )
            continue

        picks.append(best)
        taken_contracts.add((best.expiry, best.strike))
        taken_strikes.append(best.strike)
        expiry_counts[best.expiry] = expiry_counts.get(best.expiry, 0) + 1

    return picks, reasons, [evaluated[k] for k in sorted(evaluated)]


__all__ = [
    "LadderPick",
    "annualized_yield_on_margin",
    "breakeven_cushion",
    "select_put_ladder",
]
