"""Backtest engine: replays historical bars through a strategy.

Fill discipline (the single most important rule here): a signal generated
while processing bar N is queued and filled at bar N+1's OPEN price, never at
bar N's own close or open. This is what keeps the backtest honest -- a
strategy can't "see" a bar's close and then trade at that same bar's price.
A signal generated on the final bar has no N+1 to fill against and is simply
dropped.

Only long positions are modeled (BUY opens, SELL closes) -- sufficient for
the SMA-crossover / Donchian-breakout strategies this bot ships with today;
short-selling MCX commodities is a possible future extension, not implemented.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session

from growmore_bot.costs import CostModel, Side, leg_cost, slippage_price
from growmore_bot.persistence.models import BacktestRun, BacktestTrade, EquityCurvePoint
from growmore_bot.strategies.base import Signal, SignalAction, Strategy


@dataclass
class Trade:
    side: str  # "buy" (only side modeled; a trade record spans entry->exit)
    entry_price: float
    entered_at: datetime
    exit_price: Optional[float] = None
    exited_at: Optional[datetime] = None
    #: NET of transaction costs. `gross_pnl` keeps the pre-cost figure so the
    #: drag is auditable rather than something you take on trust. Slippage is
    #: already inside `entry_price`/`exit_price` -- it is a price effect, not
    #: a charge -- so it is not double-counted in `transaction_cost`.
    pnl: Optional[float] = None
    gross_pnl: Optional[float] = None
    transaction_cost: float = 0.0
    #: "stop" | "trail" | "time" | "signal" | None -- why the position was
    #: closed. Distinguishes "the strategy changed its mind" from "we were
    #: stopped out", which review very differently.
    exit_reason: Optional[str] = None
    #: Signed units held (negative = short). Recorded so a trade log can be
    #: audited for SIZE as well as price -- which is what surfaced the
    #: fixed-quantity leverage drift `size_to_equity` fixes.
    quantity: float = 0.0
    #: Internal: the exit leg's cost, so the caller can adjust cash once.
    _exit_cost: float = 0.0
    #: True when the protective stop was hit on the SAME bar the position
    #: opened. A high count means the stop is tighter than the instrument's
    #: own bar range, i.e. the backtest is measuring the stop assumption
    #: rather than the strategy.
    same_bar_stop: bool = False


@dataclass
class EquityPoint:
    ts: datetime
    equity: float


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)
    final_equity: float = 0.0
    total_transaction_cost: float = 0.0


class BacktestEngine:
    def __init__(
        self,
        strategy: Strategy,
        initial_capital: float,
        lot_size: int = 1,
        cost_model: Optional[CostModel] = None,
        tick_size: float = 0.0,
        allow_shorts: bool = False,
        size_to_equity: bool = False,
    ):
        """`lot_size` is the number of QUOTE UNITS per lot -- not raw grams/kg
        (e.g. Copper=2500 kg quoted per kg; Gold Mini is a 100g lot but MCX
        quotes it per 10g, so lot_size=10, not 100 -- see
        growmore_bot.config.CommodityPlaceholder's docstring). Defaults
        to 1 (a single raw unit of the price series) for backward compatibility;
        every existing caller/test that doesn't pass it behaves exactly as
        before. Folded into `qty` once at fill time so every downstream
        calculation (cash, mark-to-market, exit P&L) is automatically scaled --
        profit factor and win rate are unaffected (they're ratios/signs of
        uniformly-scaled trade P&Ls), only Sharpe and max drawdown change,
        since those are computed against the (unscaled) initial_capital.

        `cost_model` defaults to None, meaning ZERO transaction costs --
        bit-for-bit the behaviour every result currently in the database was
        measured with, so old and new runs stay comparable and no existing
        test changes meaning. Pass `growmore_bot.costs.DEFAULT_COST_MODEL`
        for real MCX charges. `tick_size` is only consulted when a cost model
        is supplied, since slippage is quoted in ticks.
        """
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.lot_size = lot_size
        self.cost_model = cost_model
        self.tick_size = tick_size
        #: Commodity futures are symmetric and Dhan's order client already
        #: accepts SELL, but every engine here was long-only, so the down
        #: half of every trend went uncaptured. Defaults to False so every
        #: previously stored result stays bit-for-bit reproducible.
        #: Quantity is SIGNED when enabled (negative = short), which makes
        #: each (exit - entry) * qty expression correct automatically rather
        #: than threading a +/-1 through every P&L site.
        self.allow_shorts = allow_shorts
        #: Re-derive the position size from CURRENT equity at each entry
        #: instead of always trading `lot_size` units.
        #:
        #: False (the default, and every MCX result ever published here)
        #: is right for futures: one lot is one lot. It is wrong for a long
        #: equity backtest, because a fixed share count against a stock that
        #: appreciates 100x is not a constant position -- it is escalating
        #: leverage. Re-entering 82,372 shares of a Rs 6 stock once it
        #: trades at Rs 1,000 asks for Rs 8.2 crore from an account that
        #: never had it. On the real F&O universe that produced costs of
        #: 162-352% of capital and drawdowns above 100%.
        #:
        #: Buy-and-hold is immune, since it enters once and never re-enters
        #: -- so the drift penalises precisely the arm under test and
        #: flatters the benchmark.
        self.size_to_equity = size_to_equity

    def run(self, bars: Sequence[Any]) -> BacktestResult:
        trades: list[Trade] = []
        equity_curve: list[EquityPoint] = []

        cash = float(self.initial_capital)
        position_qty = 0.0
        position_entry_price: Optional[float] = None
        open_trade: Optional[Trade] = None
        pending_signal: Optional[Signal] = None
        total_cost = 0.0
        risk_state: dict = {}

        # Stop level armed by the previous bar's signal. Deliberately only
        # ever set from CLOSED bars: a stop derived from the bar it is being
        # tested against would be lookahead.
        armed_stop: Optional[float] = None

        for bar in bars:
            stop_fired = False

            # (1) A stop is a resting level known BEFORE this bar opens, so
            # unlike a close-derived signal it is checked against THIS bar's
            # own range rather than the next one's.
            if position_qty != 0 and armed_stop is not None and open_trade is not None:
                direction = 1 if position_qty > 0 else -1
                breached = (
                    float(bar.low) <= armed_stop if direction == 1
                    else float(bar.high) >= armed_stop
                )
                if breached:
                    # A bar that GAPS through the stop never traded at it --
                    # fill at the open. min() is the whole point: booking the
                    # stop level on a gap would invent a price that was never
                    # available. MCX metals gap overnight routinely.
                    # A gap THROUGH the stop fills at the open. For a long
                    # that is the lower of the two, for a short the higher --
                    # in both cases the worse one, because the bar never
                    # traded at the stop.
                    exit_price = (
                        min(float(bar.open), armed_stop) if direction == 1
                        else max(float(bar.open), armed_stop)
                    )
                    # Credit cash at the SLIPPED fill the trade actually
                    # recorded, not the pre-slippage level, or the equity
                    # curve and the trade log quietly disagree.
                    filled, exit_cost = self._close_trade(
                        open_trade, exit_price, position_qty, bar.timestamp, "stop"
                    )
                    cash += filled * position_qty - exit_cost
                    total_cost += exit_cost
                    position_qty = 0.0
                    position_entry_price = None
                    open_trade = None
                    armed_stop = None
                    stop_fired = True

            if pending_signal is not None:
                signal, pending_signal = pending_signal, None
                reference_price = float(bar.open)

                # Reversal: an opposing signal while in a position closes it
                # and opens the other way at the SAME open, paying two legs of
                # cost. Treating it as one trade would understate the cost of
                # turning a book around.
                if (
                    self.allow_shorts
                    and position_qty != 0
                    and open_trade is not None
                    and position_entry_price is not None
                    and (
                        (position_qty > 0 and signal.action == SignalAction.SELL)
                        or (position_qty < 0 and signal.action == SignalAction.BUY)
                    )
                    and not stop_fired
                ):
                    close_side: Side = "sell" if position_qty > 0 else "buy"
                    close_price = self._fill_price(reference_price, close_side)
                    close_cost = self._leg_cost(abs(close_price * position_qty), close_side)
                    gross_pnl = (close_price - position_entry_price) * position_qty
                    open_trade.exit_price = close_price
                    open_trade.exited_at = bar.timestamp
                    open_trade.gross_pnl = gross_pnl
                    open_trade.transaction_cost += close_cost
                    open_trade.pnl = gross_pnl - open_trade.transaction_cost
                    open_trade.exit_reason = signal.exit_reason or "signal"
                    cash += close_price * position_qty - close_cost
                    total_cost += close_cost
                    position_qty = 0.0
                    position_entry_price = None
                    open_trade = None
                    armed_stop = None

                opening_short = (
                    self.allow_shorts
                    and signal.action == SignalAction.SELL
                    and position_qty == 0
                    and not stop_fired
                )
                if opening_short:
                    fill_price = self._fill_price(reference_price, "sell")
                    qty = -self._entry_qty(signal.size, fill_price, cash)
                    entry_cost = self._leg_cost(abs(fill_price * qty), "sell")
                    position_qty = qty
                    position_entry_price = fill_price
                    open_trade = Trade(
                        side="sell", entry_price=fill_price, entered_at=bar.timestamp,
                        transaction_cost=entry_cost, quantity=qty,
                    )
                    trades.append(open_trade)
                    cash -= fill_price * qty + entry_cost
                    total_cost += entry_cost
                    armed_stop = signal.stop_price
                elif signal.action == SignalAction.BUY and position_qty == 0:
                    fill_price = self._fill_price(reference_price, "buy")
                    qty = self._entry_qty(signal.size, fill_price, cash)
                    entry_cost = self._leg_cost(fill_price * qty, "buy")
                    position_qty = qty
                    position_entry_price = fill_price
                    open_trade = Trade(
                        side="buy",
                        entry_price=fill_price,
                        entered_at=bar.timestamp,
                        transaction_cost=entry_cost,
                        quantity=qty,
                    )
                    trades.append(open_trade)
                    cash -= fill_price * qty + entry_cost
                    total_cost += entry_cost
                    armed_stop = signal.stop_price
                    # You entered at this bar's open, so a stop below it
                    # genuinely was reachable within this same bar. Checking
                    # it is not lookahead -- skipping it would be optimism.
                    if armed_stop is not None and float(bar.low) <= armed_stop:  # long entry
                        filled, exit_cost = self._close_trade(
                            open_trade, armed_stop, qty, bar.timestamp, "stop"
                        )
                        open_trade.same_bar_stop = True
                        cash += filled * qty - exit_cost
                        total_cost += exit_cost
                        position_qty = 0.0
                        position_entry_price = None
                        open_trade = None
                        armed_stop = None
                        stop_fired = True
                elif (
                    signal.action
                    in (SignalAction.SELL, SignalAction.BUY)
                    and not stop_fired
                    and position_qty != 0
                    and (
                        (position_qty > 0 and signal.action == SignalAction.SELL)
                        or (position_qty < 0 and signal.action == SignalAction.BUY)
                    )
                    and open_trade is not None
                    and position_entry_price is not None
                ):
                    qty = position_qty
                    exit_side: Side = "sell" if qty > 0 else "buy"
                    fill_price = self._fill_price(reference_price, exit_side)
                    exit_cost = self._leg_cost(abs(fill_price * qty), exit_side)
                    # Slippage is already inside both fill prices, so the
                    # "gross" figure here is gross of CHARGES only -- never
                    # double-counted against the slipped prices.
                    gross_pnl = (fill_price - position_entry_price) * qty
                    open_trade.exit_price = fill_price
                    open_trade.exited_at = bar.timestamp
                    open_trade.gross_pnl = gross_pnl
                    open_trade.transaction_cost += exit_cost
                    open_trade.pnl = gross_pnl - open_trade.transaction_cost
                    open_trade.exit_reason = signal.exit_reason or "signal"
                    cash += fill_price * qty - exit_cost
                    total_cost += exit_cost
                    position_qty = 0.0
                    position_entry_price = None
                    open_trade = None
                # Any other combination (e.g. SELL with no open position, or a
                # second BUY while already long) has no valid action -- ignored.

            # Signed quantity makes this correct for a short with no special
            # case: a negative position and a falling price raise equity.
            mark_to_market = position_qty * float(bar.close) if position_qty else 0.0
            equity_curve.append(EquityPoint(ts=bar.timestamp, equity=cash + mark_to_market))

            position_state = (
                None
                if position_qty == 0
                else {
                    "quantity": position_qty,
                    "avg_entry_price": position_entry_price,
                    "risk": risk_state,
                }
            )
            new_signal = self.strategy.on_bar(bar, position_state)
            risk_state = new_signal.risk_state or ({} if position_qty == 0 else risk_state)
            # Signed: a short arms a stop ABOVE the position just as a long
            # arms one below. Gating this on `> 0` left every short
            # unprotected by the engine-level check.
            if position_qty != 0 and new_signal.stop_price is not None:
                armed_stop = new_signal.stop_price
            if new_signal.action != SignalAction.HOLD:
                pending_signal = new_signal

        final_equity = equity_curve[-1].equity if equity_curve else float(self.initial_capital)
        return BacktestResult(
            trades=trades,
            equity_curve=equity_curve,
            final_equity=final_equity,
            total_transaction_cost=total_cost,
        )

    def _close_trade(
        self, trade: Trade, exit_price: float, qty: float, ts, reason: str
    ) -> tuple[float, float]:
        """`qty` is signed; a short is closed by BUYING, so both the slippage
        direction and the statutory leg flip with it."""
        """Book an exit at an already-decided price. Used by the stop paths,
        where the fill price is the stop level or the gapped open rather than
        the next bar's open."""
        side: Side = "sell" if qty > 0 else "buy"
        slipped = exit_price
        if self.cost_model is not None:
            slipped = slippage_price(
                exit_price, side, self.tick_size, self.cost_model, is_stop=True
            )
        exit_cost = self._leg_cost(abs(slipped * qty), side)
        gross = (slipped - trade.entry_price) * qty
        trade.exit_price = slipped
        trade.exited_at = ts
        trade.gross_pnl = gross
        trade.transaction_cost += exit_cost
        trade.pnl = gross - trade.transaction_cost
        trade.exit_reason = reason
        trade._exit_cost = exit_cost
        return slipped, exit_cost

    def _entry_qty(self, signal_size: Optional[float], fill_price: float, cash: float) -> float:
        """Units to open, honouring `size_to_equity`.

        Fixed mode multiplies `lot_size`, unchanged. Equity mode spends the
        account rather than a remembered share count, and floors at one unit
        so a collapsed account can still take a position instead of silently
        ceasing to trade and freezing the equity curve.
        """
        multiplier = signal_size if signal_size else 1
        if not self.size_to_equity:
            return multiplier * self.lot_size
        if fill_price <= 0:
            return float(self.lot_size)
        return float(max(1, int((cash * multiplier) // fill_price)))

    def _fill_price(self, reference_price: float, side: Side) -> float:
        """The next-bar-open reference price, moved against us by slippage.
        Identity when no cost model is configured."""
        if self.cost_model is None:
            return reference_price
        return slippage_price(reference_price, side, self.tick_size, self.cost_model)

    def _leg_cost(self, notional: float, side: Side) -> float:
        """`side` must be passed explicitly, never inferred: notional is
        always positive here, so a sign-based guess would charge every leg as
        a buy -- silently skipping CTT (sell side only) and always applying
        stamp duty (buy side only). CTT is 0.01%, five times stamp duty, so
        that mistake would understate real costs by a material margin."""
        if self.cost_model is None:
            return 0.0
        return leg_cost(abs(notional), side, self.cost_model)

    def run_and_persist(
        self,
        bars: Sequence[Any],
        session: Session,
        strategy_id: uuid.UUID,
        instrument_id: uuid.UUID,
        started_at: datetime,
    ) -> BacktestRun:
        """Run the backtest and persist run/trades/equity-curve rows.

        Computes and stores the summary metrics (sharpe/drawdown/win-rate/
        profit-factor/cagr) on the BacktestRun row. Caller owns the
        transaction (commit/rollback) -- this only adds objects to `session`.
        """
        from growmore_bot.backtest.metrics import (
            cagr_pct,
            max_drawdown_pct,
            profit_factor,
            sharpe_ratio,
            win_rate_pct,
        )

        result = self.run(bars)

        equity_values = [p.equity for p in result.equity_curve]
        closed_pnls = [t.pnl for t in result.trades if t.pnl is not None]
        returns = [
            (b / a - 1)
            for a, b in zip(equity_values, equity_values[1:])
            if a != 0
        ]

        period_start = bars[0].timestamp if bars else started_at
        period_end = bars[-1].timestamp if bars else started_at
        years = max((period_end - period_start).days / 365.25, 0.0) if bars else 0.0

        run_row = BacktestRun(
            id=uuid.uuid4(),
            strategy_id=strategy_id,
            instrument_id=instrument_id,
            started_at=started_at,
            period_start=period_start,
            period_end=period_end,
            sharpe_ratio=sharpe_ratio(returns),
            max_drawdown_pct=max_drawdown_pct(equity_values),
            win_rate_pct=win_rate_pct(closed_pnls),
            profit_factor=(
                None if profit_factor(closed_pnls) == float("inf") else profit_factor(closed_pnls)
            ),
            cagr_pct=cagr_pct(self.initial_capital, result.final_equity, years) if years else 0.0,
            # Which capital this was measured against. Without it, a run at
            # one flat figure for every instrument (which silently means very
            # different leverage per contract, so CAGR ranks contract size as
            # much as edge) is indistinguishable from a properly normalised
            # one.
            initial_capital=self.initial_capital,
            total_transaction_cost=result.total_transaction_cost,
            gross_cagr_pct=(
                cagr_pct(
                    self.initial_capital,
                    result.final_equity + result.total_transaction_cost,
                    years,
                )
                if years
                else 0.0
            ),
            cost_model=(
                None if self.cost_model is None else asdict(self.cost_model)
            ),
        )
        session.add(run_row)

        for trade in result.trades:
            session.add(
                BacktestTrade(
                    id=uuid.uuid4(),
                    backtest_run_id=run_row.id,
                    entered_at=trade.entered_at,
                    exited_at=trade.exited_at,
                    side=trade.side,
                    entry_price=trade.entry_price,
                    exit_price=trade.exit_price,
                    pnl=trade.pnl,
                )
            )

        for point in result.equity_curve:
            session.add(
                EquityCurvePoint(
                    id=uuid.uuid4(),
                    backtest_run_id=run_row.id,
                    ts=point.ts,
                    equity=point.equity,
                )
            )

        return run_row


__all__ = ["BacktestEngine", "BacktestResult", "Trade", "EquityPoint"]
