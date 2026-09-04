"""Live trading engine -- places REAL orders via DhanOrderClient.

Deliberately mirrors growmore_bot.paper.engine.PaperTradingEngine's
interface and risk guards (max_position_size, daily_loss_limit) closely, so
a strategy behaves identically whether a bot_config is in "paper" or "live"
mode -- the only difference is a BUY/SELL signal calls a real Order API
instead of simulating a fill. Persists to LivePosition/LiveOrder (never
PaperPosition/PaperOrder) so real and simulated data can never mix.

Known approximation, documented rather than hidden: a MARKET order's
initial response only carries an order ID and an initial status (e.g.
"TRANSIT"), not a confirmed fill price -- Dhan reports the real fill
asynchronously. `avg_entry_price`/exit price here use the tick's live quote
LTP at the moment the order was placed, same convention as the paper
engine. `reconcile_pending_orders()` corrects each order's own record
against Dhan's real status/fill price once available, but does not yet
retroactively recompute a position's avg_entry_price/realized_pnl from a
corrected fill -- see docs/technical-debt.md.

Tripping the daily_loss_limit guard attempts to place a real closing order
for whatever's still open before disabling the bot_config; if that closing
order itself fails, the failure is logged and the position is left open for
manual review rather than retried automatically (see
`_trip_daily_loss_guard`).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from growmore_bot.broker.dhan_order_client import DhanOrderError
from growmore_bot.persistence.models import AuditLog, BotSignalState, LiveOrder, LivePosition
from growmore_bot.strategies.base import SignalAction, Strategy

logger = logging.getLogger(__name__)

# Order statuses that mean "nothing more will ever change" -- see Dhan's
# public order-status docs (field names not independently verified against
# the SDK source the way place_market_order's request schema was).
_TERMINAL_ORDER_STATUSES = frozenset({"TRADED", "REJECTED", "CANCELLED", "EXPIRED"})


def _format_debug_state(strategy: Strategy) -> str:
    state = strategy.debug_state()
    if not state:
        return ""
    parts = [f"{k}={v:.2f}" if v is not None else f"{k}=n/a" for k, v in state.items()]
    return "(" + " ".join(parts) + ")"


class LiveTradingEngine:
    def __init__(self, dhan_client: Any, order_client: Any, session: Any):
        self.dhan_client = dhan_client
        self.order_client = order_client
        self.session = session

    def process_tick(
        self,
        config: Any,
        instrument: Any,
        strategy: Strategy,
        current_position_qty: float = 0.0,
        avg_entry_price: Optional[float] = None,
        live_position_id: Optional[uuid.UUID] = None,
        cumulative_daily_pnl: float = 0.0,
        strategy_label: Optional[str] = None,
    ) -> None:
        label = f"LIVE {strategy_label or config.strategy_id} / {getattr(instrument, 'symbol', instrument)}"

        if not config.enabled:
            return

        now = datetime.now(timezone.utc)

        if cumulative_daily_pnl <= -float(config.daily_loss_limit):
            self._trip_daily_loss_guard(
                config,
                instrument,
                now,
                cumulative_daily_pnl,
                current_position_qty=current_position_qty,
                avg_entry_price=avg_entry_price,
                live_position_id=live_position_id,
                label=label,
            )
            return

        quote = self.dhan_client.get_quote(instrument)

        position_state = (
            None
            if current_position_qty == 0
            else {"quantity": current_position_qty, "avg_entry_price": avg_entry_price}
        )
        signal = strategy.on_bar(quote, position_state)
        computed = _format_debug_state(strategy)
        self._record_signal_state(config, signal, quote, strategy, now, cumulative_daily_pnl)

        if signal.action == SignalAction.HOLD:
            if current_position_qty != 0 and live_position_id is not None:
                position = self.session.get(LivePosition, live_position_id)
                self._mark_to_market(position, quote.ltp, instrument.lot_size)
                self.session.add(position)
            logger.info("%s -- HOLD ltp=%s %s", label, quote.ltp, computed)
            return

        size = signal.size or 1

        if signal.action == SignalAction.BUY:
            self._handle_buy(
                config, instrument, quote, current_position_qty, size, now, label, computed,
                live_position_id,
            )
        elif signal.action == SignalAction.SELL:
            self._handle_sell(
                instrument, current_position_qty, avg_entry_price, live_position_id, quote, size,
                now, label, computed,
            )

    def force_close_for_expiry(
        self,
        config: Any,
        instrument: Any,
        current_position_qty: float,
        avg_entry_price: Optional[float],
        live_position_id: Optional[uuid.UUID],
        label: str = "",
    ) -> None:
        """Force-close a REAL open position ahead of Dhan's own pre-expiry
        square-off (see growmore_bot.scheduler.contract_rollover) by placing
        a real closing SELL order. No-op if there's nothing open.
        """
        if current_position_qty <= 0 or live_position_id is None or avg_entry_price is None:
            return

        now = datetime.now(timezone.utc)
        quote = self.dhan_client.get_quote(instrument)
        try:
            placed = self.order_client.place_market_order(
                instrument, transaction_type="SELL", quantity=current_position_qty
            )
        except Exception:
            # Never let an order-placement failure propagate: session_scope()
            # rolls back the ENTIRE tick's session on any uncaught exception,
            # which would also discard the audit_log entry
            # DhanOrderClient.place_market_order already wrote for this exact
            # failure -- found live 2026-09-04 (a real DH-905 "Invalid IP"
            # rejection left literally no trace). The position is left open
            # for manual review; the next tick's close-out-cutoff check will
            # simply try again.
            logger.exception(
                "%s -- LIVE EXPIRY CLOSE-OUT order FAILED -- position remains open, will retry "
                "next tick",
                label or live_position_id,
            )
            return
        pnl = (float(quote.ltp) - float(avg_entry_price)) * current_position_qty * instrument.lot_size

        position = self.session.get(LivePosition, live_position_id)
        position.realized_pnl = float(position.realized_pnl or 0) + pnl
        position.quantity = 0
        position.status = "closed"
        position.closed_at = now
        self._mark_to_market(position, quote.ltp, instrument.lot_size)
        self.session.add(position)

        logger.warning(
            "%s -- LIVE EXPIRY CLOSE-OUT (REAL MONEY) qty=%s ltp=%s pnl=%.2f broker_order_id=%s "
            "status=%s (contract nearing MCX Tender Period)",
            label or live_position_id,
            current_position_qty,
            quote.ltp,
            pnl,
            placed.order_id,
            placed.order_status,
        )

        self.session.add(
            LiveOrder(
                id=uuid.uuid4(),
                live_position_id=live_position_id,
                side="sell",
                quantity=current_position_qty,
                broker_order_id=placed.order_id,
                order_status=placed.order_status,
                fill_price=quote.ltp,
                filled_at=now,
                pnl=pnl,
            )
        )
        self.session.add(
            AuditLog(
                id=uuid.uuid4(),
                ts=now,
                event_type="live_contract_expiry_close_out",
                payload={
                    "strategy_id": str(config.strategy_id),
                    "instrument_id": str(config.instrument_id),
                    "quantity": current_position_qty,
                    "pnl": pnl,
                    "broker_order_id": placed.order_id,
                },
            )
        )

    def reconcile_pending_orders(self) -> None:
        """Look up Dhan's real current status for every LiveOrder still in a
        non-terminal state and update `order_status`/`fill_price` to match.

        Deliberately conservative: only corrects the ORDER's own record.
        Does NOT retroactively recompute the associated LivePosition's
        avg_entry_price/realized_pnl if the real fill price differs from the
        approximate live-quote LTP used when the order was placed -- that
        needs more careful design (e.g. a position built from several blended
        fills) than this first version attempts. See docs/technical-debt.md.

        Never raises: a failed lookup for one order (network hiccup, an
        unexpected response shape) is logged and skipped, and the rest of
        the batch still gets processed. Response field names
        (`orderStatus`, `averageTradedPrice`) are Dhan's documented names
        but not independently verified against a live response -- a missing
        or renamed field just means that order is left as it was, not a
        crash.
        """
        pending_orders = (
            self.session.query(LiveOrder)
            .filter(~LiveOrder.order_status.in_(_TERMINAL_ORDER_STATUSES))
            .all()
        )
        for order in pending_orders:
            try:
                response = self.order_client.get_order_status(order.broker_order_id)
            except Exception:
                logger.exception(
                    "Failed to reconcile live order %s -- will retry next tick",
                    order.broker_order_id,
                )
                continue

            raw_data = response.get("data") if isinstance(response, dict) else None
            # Confirmed live 2026-09-04: Dhan's real GET /orders/{id} wraps
            # the order in a one-element list, not a bare dict as the
            # (unverified) public docs suggested -- support both shapes.
            if isinstance(raw_data, list):
                data = raw_data[0] if raw_data else None
            else:
                data = raw_data
            if not isinstance(data, dict):
                logger.warning(
                    "Unexpected order-status response shape for %s -- skipping",
                    order.broker_order_id,
                )
                continue

            new_status = data.get("orderStatus")
            if new_status and new_status != order.order_status:
                logger.warning(
                    "Live order %s status changed: %s -> %s",
                    order.broker_order_id,
                    order.order_status,
                    new_status,
                )
                order.order_status = new_status

            real_fill_price = data.get("averageTradedPrice")
            if real_fill_price and float(real_fill_price) > 0:
                if order.fill_price is None or float(order.fill_price) != float(real_fill_price):
                    logger.warning(
                        "Live order %s real fill price %.2f differs from the approximate %s "
                        "used at placement -- order record corrected; the position's "
                        "avg_entry_price/realized_pnl are NOT retroactively recomputed yet",
                        order.broker_order_id,
                        float(real_fill_price),
                        order.fill_price,
                    )
                order.fill_price = real_fill_price

            self.session.add(order)

    def _record_signal_state(
        self,
        config: Any,
        signal: Any,
        quote: Any,
        strategy: Strategy,
        now: datetime,
        cumulative_daily_pnl: float = 0.0,
    ) -> None:
        """Upsert this bot_config's "what did the strategy just see" row --
        same convention as PaperTradingEngine, shared by both paper and live
        configs so the dashboard doesn't need to care which mode a config is
        in to show its current signal.
        """
        existing = (
            self.session.query(BotSignalState).filter_by(bot_config_id=config.id).one_or_none()
        )
        if existing is None:
            self.session.add(
                BotSignalState(
                    id=uuid.uuid4(),
                    bot_config_id=config.id,
                    last_signal=signal.action.value,
                    checked_at=now,
                    ltp=quote.ltp,
                    prev_close=getattr(quote, "close", None),
                    daily_pnl=cumulative_daily_pnl,
                    indicators=strategy.debug_state(),
                    crossing_state=strategy.get_state_snapshot(),
                )
            )
        else:
            existing.last_signal = signal.action.value
            existing.checked_at = now
            existing.ltp = quote.ltp
            existing.prev_close = getattr(quote, "close", None)
            existing.daily_pnl = cumulative_daily_pnl
            existing.indicators = strategy.debug_state()
            existing.crossing_state = strategy.get_state_snapshot()
            self.session.add(existing)

    @staticmethod
    def _mark_to_market(position: Any, ltp: Any, lot_size: int) -> None:
        position.unrealized_pnl = (
            (float(ltp) - float(position.avg_entry_price)) * float(position.quantity) * lot_size
        )

    def _trip_daily_loss_guard(
        self,
        config: Any,
        instrument: Any,
        now: datetime,
        cumulative_daily_pnl: float,
        current_position_qty: float = 0.0,
        avg_entry_price: Optional[float] = None,
        live_position_id: Optional[uuid.UUID] = None,
        label: str = "",
    ) -> None:
        config.enabled = False

        auto_close_attempted = False
        auto_close_succeeded = False
        close_pnl: Optional[float] = None

        has_open_position = (
            current_position_qty > 0 and live_position_id is not None and avg_entry_price is not None
        )
        if has_open_position:
            auto_close_attempted = True
            try:
                quote = self.dhan_client.get_quote(instrument)
                placed = self.order_client.place_market_order(
                    instrument, transaction_type="SELL", quantity=current_position_qty
                )
                close_pnl = (
                    (float(quote.ltp) - float(avg_entry_price)) * current_position_qty
                    * instrument.lot_size
                )
                position = self.session.get(LivePosition, live_position_id)
                position.realized_pnl = float(position.realized_pnl or 0) + close_pnl
                position.quantity = 0
                position.status = "closed"
                position.closed_at = now
                self._mark_to_market(position, quote.ltp, instrument.lot_size)
                self.session.add(position)
                self.session.add(
                    LiveOrder(
                        id=uuid.uuid4(),
                        live_position_id=live_position_id,
                        side="sell",
                        quantity=current_position_qty,
                        broker_order_id=placed.order_id,
                        order_status=placed.order_status,
                        fill_price=quote.ltp,
                        filled_at=now,
                        pnl=close_pnl,
                    )
                )
                auto_close_succeeded = True
                logger.warning(
                    "%s -- DAILY LOSS LIMIT TRIPPED, auto-closed the open REAL position "
                    "(REAL MONEY) qty=%s pnl=%.2f broker_order_id=%s",
                    label or config.strategy_id,
                    current_position_qty,
                    close_pnl,
                    placed.order_id,
                )
            except Exception:
                logger.exception(
                    "%s -- DAILY LOSS LIMIT TRIPPED but the auto-close order FAILED -- the "
                    "REAL position remains open and needs manual review",
                    label or config.strategy_id,
                )
        else:
            logger.warning(
                "%s -- DAILY LOSS LIMIT TRIPPED, disabling (cumulative_daily_pnl=%.2f, "
                "daily_loss_limit=%.2f) -- no open position to close",
                label or config.strategy_id,
                cumulative_daily_pnl,
                float(config.daily_loss_limit),
            )

        self.session.add(
            AuditLog(
                id=uuid.uuid4(),
                ts=now,
                event_type="live_risk_guard_daily_loss_limit_tripped",
                payload={
                    "strategy_id": str(config.strategy_id),
                    "instrument_id": str(config.instrument_id),
                    "cumulative_daily_pnl": cumulative_daily_pnl,
                    "daily_loss_limit": float(config.daily_loss_limit),
                    "auto_close_attempted": auto_close_attempted,
                    "auto_close_succeeded": auto_close_succeeded,
                    "auto_close_pnl": close_pnl,
                },
            )
        )

    def _handle_buy(
        self,
        config: Any,
        instrument: Any,
        quote: Any,
        current_position_qty: float,
        size: float,
        now: datetime,
        label: str = "",
        computed: str = "",
        live_position_id: Optional[uuid.UUID] = None,
    ) -> None:
        new_total = current_position_qty + size
        if new_total > float(config.max_position_size):
            logger.warning(
                "%s -- BUY REJECTED (max_position_size): requested_total=%s max_position_size=%s %s",
                label or config.strategy_id,
                new_total,
                float(config.max_position_size),
                computed,
            )
            self.session.add(
                AuditLog(
                    id=uuid.uuid4(),
                    ts=now,
                    event_type="live_risk_guard_max_position_size_rejected",
                    payload={
                        "strategy_id": str(config.strategy_id),
                        "instrument_id": str(config.instrument_id),
                        "requested_total": new_total,
                        "max_position_size": float(config.max_position_size),
                    },
                )
            )
            return

        try:
            placed = self.order_client.place_market_order(
                instrument, transaction_type="BUY", quantity=size
            )
        except Exception:
            # See force_close_for_expiry's comment: never let this propagate
            # up to session_scope()'s rollback, which would also discard the
            # audit_log entry the order client already wrote for the
            # failure. No position/order is recorded -- nothing was filled.
            logger.exception(
                "%s -- LIVE BUY order FAILED (qty=%s) -- no position opened",
                label or config.strategy_id,
                size,
            )
            return

        logger.warning(
            "%s -- LIVE BUY PLACED (REAL MONEY) qty=%s ltp=%s broker_order_id=%s status=%s %s",
            label or config.strategy_id,
            size,
            quote.ltp,
            placed.order_id,
            placed.order_status,
            computed,
        )

        if current_position_qty == 0 or live_position_id is None:
            position_id = uuid.uuid4()
            self.session.add(
                LivePosition(
                    id=position_id,
                    strategy_id=config.strategy_id,
                    instrument_id=instrument.id,
                    status="open",
                    quantity=size,
                    avg_entry_price=quote.ltp,
                    realized_pnl=0,
                    unrealized_pnl=0,
                    opened_at=now,
                    closed_at=None,
                )
            )
        else:
            position_id = live_position_id
            position = self.session.get(LivePosition, live_position_id)
            blended_avg = (
                float(position.avg_entry_price) * current_position_qty + float(quote.ltp) * size
            ) / new_total
            position.avg_entry_price = blended_avg
            position.quantity = new_total
            self._mark_to_market(position, quote.ltp, instrument.lot_size)
            self.session.add(position)

        self.session.add(
            LiveOrder(
                id=uuid.uuid4(),
                live_position_id=position_id,
                side="buy",
                quantity=size,
                broker_order_id=placed.order_id,
                order_status=placed.order_status,
                fill_price=quote.ltp,
                filled_at=now,
            )
        )

    def _handle_sell(
        self,
        instrument: Any,
        current_position_qty: float,
        avg_entry_price: Optional[float],
        live_position_id: Optional[uuid.UUID],
        quote: Any,
        size: float,
        now: datetime,
        label: str = "",
        computed: str = "",
    ) -> None:
        if current_position_qty <= 0 or live_position_id is None or avg_entry_price is None:
            return  # Nothing open to close -- no shorting.

        close_qty = min(size, current_position_qty)
        remaining_qty = current_position_qty - close_qty

        try:
            placed = self.order_client.place_market_order(
                instrument, transaction_type="SELL", quantity=close_qty
            )
        except Exception:
            # See force_close_for_expiry's comment: never let this propagate
            # up to session_scope()'s rollback. Position is left exactly as
            # it was -- nothing was actually closed.
            logger.exception(
                "%s -- LIVE SELL order FAILED (qty=%s) -- position unchanged",
                label or live_position_id,
                close_qty,
            )
            return

        pnl = (float(quote.ltp) - float(avg_entry_price)) * close_qty * instrument.lot_size

        position = self.session.get(LivePosition, live_position_id)
        position.realized_pnl = float(position.realized_pnl or 0) + pnl
        position.quantity = remaining_qty
        if remaining_qty <= 0:
            position.status = "closed"
            position.closed_at = now
        self._mark_to_market(position, quote.ltp, instrument.lot_size)
        self.session.add(position)

        logger.warning(
            "%s -- LIVE SELL PLACED (REAL MONEY) qty=%s ltp=%s pnl=%.2f broker_order_id=%s "
            "status=%s %s %s",
            label or live_position_id,
            close_qty,
            quote.ltp,
            pnl,
            placed.order_id,
            placed.order_status,
            "(position closed)" if remaining_qty <= 0 else f"(remaining_qty={remaining_qty})",
            computed,
        )

        self.session.add(
            LiveOrder(
                id=uuid.uuid4(),
                live_position_id=live_position_id,
                side="sell",
                quantity=close_qty,
                broker_order_id=placed.order_id,
                order_status=placed.order_status,
                fill_price=quote.ltp,
                filled_at=now,
                pnl=pnl,
            )
        )


__all__ = ["LiveTradingEngine"]
