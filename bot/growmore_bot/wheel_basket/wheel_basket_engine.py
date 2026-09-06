"""The wheel-basket paper-trading decision engine.

Runs once per trading day (see growmore_bot.scheduler.run for the scheduled
job), not every 5-minute tick like the MCX paper/live engines -- an options
wheel is decided at expiry, not intraday. Market data (spot, IV, RSI, MACD
stance, tradeable strike/premium pairs) is handed in as `CandidateData`,
mirroring how the offline backtest's `run_wheel`
(research/stock_options/wheel_engine.py) takes a pre-sliced day_chain rather
than fetching live -- this keeps the DECISION logic here fully unit
testable without a Dhan connection. Fetching that data from Dhan's real-time
option chain is a separate concern (growmore_bot/wheel_basket/live_iv_rank.py).

THE STATE MACHINE per (config, symbol) position, same shape as the backtest's
(research/stock_options/wheel_engine.py's module docstring), plus a rotation
decision the backtest never needed (single-stock backtests don't rotate):

    (no position) -- sell put --------------------> short_put
    short_put ---- put expires OTM ---------------> re-sell put on the SAME
                                                     stock, UNLESS another
                                                     eligible candidate's
                                                     score clears the
                                                     rotation_hysteresis_pct
                                                     margin, in which case
                                                     this position closes and
                                                     a new one opens on the
                                                     winner instead
    short_put ---- put expires ITM ---------------> holding_shares (real
                                                     shares at the strike;
                                                     that strike is basis)
    holding_shares - covered call written ---------> short_call
    short_call ---- call expires OTM --------------> holding_shares (keep
                                                     premium, write another
                                                     next cycle)
    short_call ---- call expires ITM --------------> position closed
                                                     (called away)

STOCK SELECTION uses only the validated metric from
docs/stock-options-results.md Sec 7.1 -- cross-sectional IV percentile rank
(growmore_bot.wheel_basket.scoring.iv_percentile_ranks) -- restricted to the
top `config.top_iv_frac`. RSI/MACD are NOT part of the selection score (see
scoring.py's docstring); RSI separately drives the covered-call basis buffer
(growmore_bot.options.strike_selection.rsi_scaled_basis_buffer) once a stock
is held, which is a different decision from which stock to enter.

CAPITAL is one pool (`config.total_virtual_capital`), divided evenly across
however many currently-eligible, not-yet-held candidates there is room to
open this cycle -- "sized dynamically", not a fixed slot count, per the
project owner's explicit choice.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional

import pandas as pd

from growmore_bot.options.strike_selection import rsi_scaled_basis_buffer, select_strike
from growmore_bot.persistence.models import (
    WheelBasketLeg,
    WheelBasketPosition,
    WheelBasketSelection,
)
from growmore_bot.wheel_basket.scoring import (
    eligible_candidates,
    iv_percentile_ranks,
    reason_for,
    should_rotate,
)


@dataclass(frozen=True)
class CandidateData:
    """One stock's market data for this cycle -- everything the engine
    needs to decide with, already fetched and liquidity-filtered by the
    caller (see live_iv_rank.py).
    """

    symbol: str
    spot: float
    avg_iv: float
    lot_size: int = 1
    rsi: Optional[float] = None
    macd_bullish: Optional[bool] = None
    #: Tradeable (strike, premium) pairs for a NEW put entry this cycle.
    put_chain: tuple[tuple[float, float], ...] = ()
    #: Tradeable (strike, premium) pairs for a covered call, for a symbol
    #: currently holding shares.
    call_chain: tuple[tuple[float, float], ...] = ()
    cycle_expiry: Optional[date] = None


def _pick_strike(
    chain: tuple[tuple[float, float], ...],
    spot: float,
    target_otm: float,
    floor_strike: Optional[float] = None,
) -> Optional[tuple[float, float]]:
    """Reuses `select_strike` (the same logic the backtest and every other
    strategy tested in docs/stock-options-results.md runs on) rather than
    re-implementing strike selection a second time for the live engine.
    """
    if not chain:
        return None
    frame = pd.DataFrame(
        {
            "strike": [s for s, _ in chain],
            "opt_type": ["X"] * len(chain),
            "volume": [1] * len(chain),
            "settle": [p for _, p in chain],
        }
    )
    row = select_strike(frame, "X", spot, target_otm, floor_strike=floor_strike)
    if row is None:
        return None
    return float(row["strike"]), float(row["settle"])


class WheelBasketEngine:
    def __init__(self, session: Any):
        self.session = session

    def run_cycle(
        self,
        config: Any,
        candidates: dict[str, CandidateData],
        today: date,
    ) -> None:
        now = datetime.now(timezone.utc)
        needs_new_leg: dict[str, WheelBasketPosition] = {}

        open_positions = (
            self.session.query(WheelBasketPosition)
            .filter_by(config_id=config.id, status="open")
            .all()
        )
        for position in open_positions:
            cand = candidates.get(position.symbol)
            open_leg = (
                self.session.query(WheelBasketLeg)
                .filter_by(position_id=position.id, settled_at=None)
                .one_or_none()
            )
            if open_leg is None or cand is None or open_leg.cycle_expiry != today:
                continue  # not this cycle's decision point for this position

            self._settle_leg(position, open_leg, cand, now)
            if position.status == "open":
                needs_new_leg[position.symbol] = position

        percentiles = iv_percentile_ranks({s: c.avg_iv for s, c in candidates.items()})
        eligible = eligible_candidates(percentiles, float(config.top_iv_frac))

        for symbol, position in needs_new_leg.items():
            cand = candidates[symbol]
            if position.state == "holding_shares":
                self._write_covered_call(position, cand, now)
            else:
                self._decide_put_rotation(
                    config, position, symbol, cand, percentiles, eligible, now,
                )

        self._fill_empty_capital(config, candidates, eligible, percentiles, now)
        self._record_selections(config, candidates, percentiles, today)

    def _settle_leg(
        self, position: WheelBasketPosition, leg: WheelBasketLeg, cand: CandidateData, now: datetime,
    ) -> None:
        spot = cand.spot
        leg.settled_at = now
        if leg.opt_type == "PE":
            if spot < leg.strike:
                leg.assigned = True
                position.state = "holding_shares"
                position.basis = leg.strike
                position.shares = float(position.lots) * cand.lot_size
            # else: expired OTM, still flat -- rotation/re-sell decided by caller.
        else:  # CE
            if spot > leg.strike:
                leg.called_away = True
                position.status = "closed"
                position.closed_at = now
                position.shares = 0
                position.state = "flat"
            else:
                position.state = "holding_shares"  # still holding, needs a new call

    def _write_covered_call(
        self, position: WheelBasketPosition, cand: CandidateData, now: datetime,
    ) -> None:
        assert position.basis is not None, "holding_shares position must have a basis"
        buffer_pct = rsi_scaled_basis_buffer(cand.rsi)
        floor = float(position.basis) * (1 + buffer_pct)
        picked = _pick_strike(cand.call_chain, cand.spot, target_otm=0.0, floor_strike=floor)
        if picked is None:
            # No call clears the floor -- held uncovered this cycle, exactly
            # the "frozen" outcome research.stock_options.wheel_engine
            # records rather than quietly relaxing the no-loss rule.
            return
        strike, premium = picked
        self.session.add(
            WheelBasketLeg(
                id=uuid.uuid4(), position_id=position.id, cycle_expiry=cand.cycle_expiry,
                opt_type="CE", strike=strike, premium=premium, lots=position.lots,
                action="sell_call", opened_at=now,
            )
        )
        position.state = "short_call"

    def _decide_put_rotation(
        self,
        config: Any,
        position: WheelBasketPosition,
        symbol: str,
        cand: CandidateData,
        percentiles: dict[str, float],
        eligible: set[str],
        now: datetime,
    ) -> None:
        current_score = percentiles.get(symbol, 0.0)
        challengers = {s: p for s, p in percentiles.items() if s in eligible and s != symbol}
        best: Optional[tuple[str, float]] = (
            max(challengers.items(), key=lambda kv: kv[1]) if challengers else None
        )

        if best is not None and should_rotate(
            current_score, best[1], float(config.rotation_hysteresis_pct)
        ):
            position.status = "closed"
            position.closed_at = now
            position.state = "flat"
            # The winning candidate is opened by _fill_empty_capital below,
            # since it needs to compete for capital the same way any other
            # empty slot does.
            return

        picked = _pick_strike(cand.put_chain, cand.spot, target_otm=0.0)
        if picked is None:
            return
        strike, premium = picked
        self.session.add(
            WheelBasketLeg(
                id=uuid.uuid4(), position_id=position.id, cycle_expiry=cand.cycle_expiry,
                opt_type="PE", strike=strike, premium=premium, lots=position.lots,
                action="sell_put", opened_at=now,
            )
        )
        position.state = "short_put"

    def _fill_empty_capital(
        self,
        config: Any,
        candidates: dict[str, CandidateData],
        eligible: set[str],
        percentiles: dict[str, float],
        now: datetime,
    ) -> None:
        currently_open = {
            p.symbol
            for p in self.session.query(WheelBasketPosition)
            .filter_by(config_id=config.id, status="open")
            .all()
        }
        available = sorted(
            (s for s in eligible if s not in currently_open),
            key=lambda s: -percentiles[s],
        )
        if not available:
            return
        capital_per_slot = float(config.total_virtual_capital) / len(available)
        for symbol in available:
            cand = candidates[symbol]
            picked = _pick_strike(cand.put_chain, cand.spot, target_otm=0.0)
            if picked is None:
                continue
            strike, premium = picked
            lots = max(1, int(capital_per_slot // (strike * cand.lot_size)))
            position_id = uuid.uuid4()
            self.session.add(
                WheelBasketPosition(
                    id=position_id, config_id=config.id, symbol=symbol, status="open",
                    state="short_put", basis=None, shares=0, lots=lots, opened_at=now,
                    realized_pnl=0, unrealized_pnl=0,
                )
            )
            self.session.add(
                WheelBasketLeg(
                    id=uuid.uuid4(), position_id=position_id, cycle_expiry=cand.cycle_expiry,
                    opt_type="PE", strike=strike, premium=premium, lots=lots,
                    action="sell_put", opened_at=now,
                )
            )

    def _record_selections(
        self,
        config: Any,
        candidates: dict[str, CandidateData],
        percentiles: dict[str, float],
        today: date,
    ) -> None:
        final_open = {
            p.symbol
            for p in self.session.query(WheelBasketPosition)
            .filter_by(config_id=config.id, status="open")
            .all()
        }
        for symbol, cand in candidates.items():
            iv_pctile = percentiles.get(symbol, 0.0)
            selected = symbol in final_open
            self.session.add(
                WheelBasketSelection(
                    id=uuid.uuid4(), config_id=config.id, cycle_date=today, symbol=symbol,
                    selected=selected, avg_iv=cand.avg_iv, iv_percentile=iv_pctile,
                    rsi=cand.rsi, macd_bullish=cand.macd_bullish, score=iv_pctile,
                    reason=reason_for(symbol, iv_pctile, cand.rsi, cand.macd_bullish, selected),
                )
            )


__all__ = ["CandidateData", "WheelBasketEngine"]
