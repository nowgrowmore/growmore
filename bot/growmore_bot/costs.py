"""Real MCX transaction costs and slippage.

Until this existed, every P&L in the repo -- backtest, paper and live -- was
a bare price difference against a cost load of exactly zero. That is fine as
a first approximation for a strategy holding for weeks and trading ~18 times
a year, and actively misleading for anything that trades more often, which
is precisely the comparison a trend ensemble or any intraday idea needs to
survive.

Rates (Dhan / MCX commodity futures, checked 2026-09-05):
  - brokerage       Rs 20 per executed order, or 0.03% of turnover,
                    WHICHEVER IS LOWER. The Rs 20 cap binds above
                    Rs 66,667 of notional, i.e. for essentially every MCX
                    contract -- which is why the flat fee makes SMALL
                    contracts proportionally the expensive ones.
  - exchange txn    ~0.0026% of turnover
  - CTT             0.01% of turnover, SELL side only (non-agri futures)
  - stamp duty      0.002% of turnover, BUY side only
  - SEBI turnover   Rs 20 per crore (0.0002%)
  - GST             18%, on brokerage + exchange + SEBI. NOT on CTT or stamp.

Slippage is modelled in TICKS, not basis points, and that distinction
matters more than the whole statutory table: one Copper tick is Rs 0.05 on
2,500 kg = Rs 125 a lot, so two ticks a side is Rs 500 a round trip against
Rs 688 of statutory charges. A flat basis-point assumption would rank the
instruments backwards -- Copper looks cheap in bps precisely because its
notional is enormous.

`stop_slippage_ticks` is charged ON TOP of ordinary slippage for a stop
fill, and it is not a decoration. This bot has no resting stop order: it
polls every 5 minutes and `DhanClient` is hard-limited to read-only Data
APIs, so a "stop" is a software stop that fires at the next poll's price,
not at the stop level. A backtest that fills stops at the stop level would
systematically flatter every stop-based strategy relative to what this
system can actually execute.

Stdlib only and pure, matching `growmore_bot.backtest.metrics` -- so the
same model runs in the backtest and in the live engines, and every number
is hand-checkable in a test.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Side = Literal["buy", "sell"]

_VALID_SIDES = ("buy", "sell")


@dataclass(frozen=True)
class CostModel:
    brokerage_per_order: float = 20.0
    brokerage_pct: float = 0.0003
    exchange_txn_pct: float = 0.000026
    ctt_sell_pct: float = 0.0001
    #: Securities Transaction Tax, charged on BOTH legs -- unlike
    #: `ctt_sell_pct`, which is sell-only. Default 0.0 so every MCX
    #: number ever published here is unchanged to the decimal; only
    #: NSE_EQUITY_DELIVERY_COST_MODEL sets it.
    stt_both_pct: float = 0.0
    stamp_buy_pct: float = 0.00002
    sebi_pct: float = 0.000002
    gst_pct: float = 0.18
    slippage_ticks: float = 2.0
    stop_slippage_ticks: float = 2.0


#: Shared default so callers don't each construct their own and drift.
DEFAULT_COST_MODEL = CostModel()

#: NSE cash-equity DELIVERY costs (Dhan, checked 2026-09-05). Used only by
#: the F&O-universe research in bot/research/fno/ -- the bot itself trades
#: MCX and must keep DEFAULT_COST_MODEL.
#:
#: The one line that matters: **STT is 0.1% on BOTH legs**, where MCX pays
#: CTT 0.01% on the sell alone. That is ~20bps a round trip against ~1bp,
#: and against a config closing ~20 trades a year it is roughly 4%/yr of
#: drag. Reusing DEFAULT_COST_MODEL for equities would understate the true
#: cost by an order of magnitude and flatter every result.
#:
#: Slippage stays in TICKS rather than basis points, and that is more
#: correct here than it is on MCX, not less: a Rs 15 F&O name genuinely has
#: a Rs 0.05 spread, so a bps assumption would price cheap stocks as if
#: they were as tight as a Rs 3,000 one.
#:
#: NOT modelled: the depository's ~Rs 15 per-scrip debit on each sell. It is
#: a flat per-scrip charge that `round_trip_cost` has nowhere to put, and at
#: Rs 5 lakh a position it is 0.3bps -- two orders of magnitude below STT.
#: Recorded here rather than silently dropped.
NSE_EQUITY_DELIVERY_COST_MODEL = CostModel(
    brokerage_per_order=0.0,   # Dhan charges nothing for delivery equity
    brokerage_pct=0.0,
    stt_both_pct=0.001,        # 0.1%, buy AND sell -- the dominant charge
    exchange_txn_pct=0.0000297,  # NSE 0.00297% of turnover
    ctt_sell_pct=0.0,          # CTT is commodities only
    stamp_buy_pct=0.00015,     # 0.015%, buy side only
    sebi_pct=0.000001,         # Rs 10 per crore
    gst_pct=0.18,              # on brokerage + exchange + SEBI only
    slippage_ticks=2.0,
    stop_slippage_ticks=2.0,
)


#: A model that charges nothing -- the explicit way to reproduce the
#: pre-cost behaviour of every existing backtest, rather than passing None
#: around and branching on it.
FREE_COST_MODEL = CostModel(
    brokerage_per_order=0.0,
    brokerage_pct=0.0,
    exchange_txn_pct=0.0,
    ctt_sell_pct=0.0,
    stt_both_pct=0.0,
    stamp_buy_pct=0.0,
    sebi_pct=0.0,
    gst_pct=0.0,
    slippage_ticks=0.0,
    stop_slippage_ticks=0.0,
)


@dataclass(frozen=True)
class RoundTripCost:
    statutory: float
    slippage: float

    @property
    def total(self) -> float:
        return self.statutory + self.slippage

    def bps_of_notional(self, notional: float) -> float:
        if notional <= 0:
            return 0.0
        return self.total / notional * 10_000


def leg_cost(notional: float, side: Side, model: CostModel = DEFAULT_COST_MODEL) -> float:
    """Statutory cost of ONE leg (buy or sell) on `notional` rupees of
    turnover. Excludes slippage, which is a price effect rather than a
    charge -- see `slippage_price`.
    """
    if side not in _VALID_SIDES:
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    if notional < 0:
        raise ValueError("notional must not be negative")

    brokerage = min(model.brokerage_per_order, notional * model.brokerage_pct)
    exchange = notional * model.exchange_txn_pct
    sebi = notional * model.sebi_pct
    # GST applies to the service charges only, never to CTT or stamp duty.
    gst = model.gst_pct * (brokerage + exchange + sebi)
    ctt = notional * model.ctt_sell_pct if side == "sell" else 0.0
    stamp = notional * model.stamp_buy_pct if side == "buy" else 0.0
    # STT, like CTT and stamp, is a tax and carries no GST.
    stt = notional * model.stt_both_pct
    return brokerage + exchange + sebi + gst + ctt + stamp + stt


def slippage_cost(
    tick_size: float, lot_size: int, lots: float, model: CostModel = DEFAULT_COST_MODEL,
    is_stop: bool = False,
) -> float:
    """Rupee cost of slippage on ONE leg, in ticks scaled to the position."""
    ticks = model.slippage_ticks + (model.stop_slippage_ticks if is_stop else 0.0)
    return ticks * tick_size * lot_size * abs(lots)


def round_trip_cost(
    notional: float,
    tick_size: float,
    lot_size: int,
    lots: float,
    model: CostModel = DEFAULT_COST_MODEL,
    exit_is_stop: bool = False,
) -> RoundTripCost:
    """Full in-and-out cost for a position of `lots` lots. `notional` is the
    TOTAL turnover per leg (price * lot_size * lots), not per lot."""
    statutory = leg_cost(notional, "buy", model) + leg_cost(notional, "sell", model)
    slip = slippage_cost(tick_size, lot_size, lots, model) + slippage_cost(
        tick_size, lot_size, lots, model, is_stop=exit_is_stop
    )
    return RoundTripCost(statutory=statutory, slippage=slip)


def slippage_price(
    ref_price: float,
    side: Side,
    tick_size: float,
    model: CostModel = DEFAULT_COST_MODEL,
    is_stop: bool = False,
) -> float:
    """The price actually assumed filled: a buy pays up, a sell gets hit
    down, both by `slippage_ticks` (plus `stop_slippage_ticks` on a stop).
    """
    if side not in _VALID_SIDES:
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    ticks = model.slippage_ticks + (model.stop_slippage_ticks if is_stop else 0.0)
    offset = ticks * tick_size
    return ref_price + offset if side == "buy" else ref_price - offset


__all__ = [
    "CostModel",
    "DEFAULT_COST_MODEL",
    "FREE_COST_MODEL",
    "RoundTripCost",
    "leg_cost",
    "slippage_cost",
    "round_trip_cost",
    "slippage_price",
]
