"""Paper trading engine.

On each scheduler tick, for a given (bot_config, instrument, strategy):
  1. Fetch the latest quote via the Dhan Data API client.
  2. Feed it to the strategy's `on_bar` (live LTP takes the place of a bar's
     close -- there's no lookahead concern here since it's live data, unlike
     the backtest engine which must fill at the *next* bar's open).
  3. If a BUY/SELL signal fires, simulate a fill AT THE FETCHED LTP.
  4. Enforce risk guards:
       - max_position_size: reject (no-op) any fill that would push the
         resulting position size over the configured limit.
       - daily_loss_limit: if cumulative realized P&L for the day has
         breached the limit, flip `bot_config.enabled` to False and write an
         `audit_log` entry -- checked before anything else on a tick, so a
         tripped guard skips the rest of that tick's trading entirely.

This module never calls Dhan's Order API -- `dhan_client` is expected to be a
growmore_bot.broker.dhan_client.DhanClient (or a mock of one in tests), whose
wrapper only exposes Data API methods in the first place.

Units: `size` (from Signal.size, default 1) and every `quantity`/
`max_position_size` value are in human-friendly LOT units -- "2" means 2
lots, matching what someone configuring bot_config would expect, not 2 raw
grams/kg/barrels. `instrument.lot_size` (the real MCX contract unit, e.g.
Gold Mini=100g) only scales the computed rupee P&L on a sell/close -- the
same real-vs-raw-unit fix already applied to BacktestEngine (see
docs/technical-debt.md). Found and fixed together with a second real gap:
`_handle_sell` used to only ever write a PaperOrder row and never actually
closed the PaperPosition or recorded realized P&L, so a position would
stay "open" forever with nothing tracked.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from growmore_bot.persistence.models import AuditLog, BotSignalState, PaperOrder, PaperPosition
from growmore_bot.strategies.base import SignalAction, Strategy

logger = logging.getLogger(__name__)

# See bot_signal_state.last_max_position_rejection_logged_at's migration:
# a genuinely repeated crossing while already at max position size is real,
# but low marginal audit-log value once already recorded recently.
_MAX_POSITION_REJECTION_THROTTLE = timedelta(minutes=30)


def _should_log_max_position_rejection(session: Any, config_id: Any, now: datetime) -> bool:
    """Throttle repeat `..._max_position_size_rejected` audit_log entries
    for the same bot_config to once per 30 minutes. Updates
    `last_max_position_rejection_logged_at` on that config's BotSignalState
    row when it decides to log. bot.log's own warning is unaffected -- only
    the audit_log write is throttled. Defensive against a session/state
    that isn't a real BotSignalState (e.g. an unconfigured test mock) --
    always logs in that case, same as before this throttle existed.
    """
    signal_state = session.query(BotSignalState).filter_by(bot_config_id=config_id).one_or_none()
    if not isinstance(signal_state, BotSignalState):
        return True
    last_logged = signal_state.last_max_position_rejection_logged_at
    if isinstance(last_logged, datetime):
        # SQLite (unit tests) round-trips a tz-aware datetime as naive --
        # normalize both sides before subtracting.
        last_logged_naive = last_logged.replace(tzinfo=None) if last_logged.tzinfo else last_logged
        now_naive = now.replace(tzinfo=None) if now.tzinfo else now
        if now_naive - last_logged_naive < _MAX_POSITION_REJECTION_THROTTLE:
            return False
    signal_state.last_max_position_rejection_logged_at = now
    session.add(signal_state)
    return True


def _format_debug_state(strategy: Strategy) -> str:
    """Render a strategy's `debug_state()` as e.g. "(macd=-12.34 signal=5.67)"
    for inline logging -- so a log line shows *why* a signal did or didn't
    fire, not just that it didn't. Empty string if the strategy exposes
    nothing (debug_state() defaults to {}).
    """
    state = strategy.debug_state()
    if not state:
        return ""
    parts = [f"{k}={v:.2f}" if v is not None else f"{k}=n/a" for k, v in state.items()]
    return "(" + " ".join(parts) + ")"


class PaperTradingEngine:
    def __init__(self, dhan_client: Any, session: Any):
        self.dhan_client = dhan_client
        self.session = session

    def process_tick(
        self,
        config: Any,
        instrument: Any,
        strategy: Strategy,
        current_position_qty: float = 0.0,
        avg_entry_price: Optional[float] = None,
        paper_position_id: Optional[uuid.UUID] = None,
        cumulative_daily_pnl: float = 0.0,
        strategy_label: Optional[str] = None,
    ) -> None:
        # Human-readable identifier for log lines -- "macd_trend v1.0 / GOLDM"
        # instead of raw UUIDs. `strategy_label` (ideally "name vversion", from
        # the caller's Strategy row -- see scheduler/run.py) falls back to the
        # bare strategy_id when not provided (e.g. in tests that don't care).
        label = f"{strategy_label or config.strategy_id} / {getattr(instrument, 'symbol', instrument)}"

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
                paper_position_id=paper_position_id,
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
            # Mark the open position to market against this tick's real
            # quote -- HOLD is by far the most common outcome, and until
            # this was added, unrealized_pnl was only ever written once (as
            # 0) at open and once more (also 0) at close, so it never
            # reflected real market movement in between. Found via the
            # dashboard showing every open position stuck at zero
            # unrealized P&L.
            if current_position_qty != 0 and paper_position_id is not None:
                position = self.session.get(PaperPosition, paper_position_id)
                self._mark_to_market(position, quote.ltp, instrument.lot_size)
                self.session.add(position)
            # INFO, not DEBUG: fires once per tick per enabled config (every
            # 5 minutes by default), so it's a cheap, useful "still alive and
            # checking" signal in bot.log, not log spam.
            logger.info("%s -- HOLD ltp=%s %s", label, quote.ltp, computed)
            return

        size = signal.size or 1

        if signal.action == SignalAction.BUY:
            self._handle_buy(
                config, instrument, quote, current_position_qty, size, now, label, computed,
                paper_position_id,
            )
        elif signal.action == SignalAction.SELL:
            self._handle_sell(
                instrument, current_position_qty, avg_entry_price, paper_position_id, quote, size,
                now, label, computed,
            )

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
        lets the dashboard show live HOLD/BUY/SELL status and the computed
        indicator values without grepping bot.log. One row per bot_config,
        always overwritten with the latest tick, not a history log.
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
        """Recompute a position's unrealized P&L against a real quote.

        Safe to call with quantity=0 (e.g. right after a full close) --
        yields 0, which is the correct value.
        """
        position.unrealized_pnl = (
            (float(ltp) - float(position.avg_entry_price)) * float(position.quantity) * lot_size
        )

    def force_close_for_expiry(
        self,
        config: Any,
        instrument: Any,
        current_position_qty: float,
        avg_entry_price: Optional[float],
        paper_position_id: Optional[uuid.UUID],
        label: str = "",
    ) -> None:
        """Force-close an open position ahead of Dhan's own pre-expiry
        square-off (see growmore_bot.scheduler.contract_rollover) --
        compulsory-delivery MCX contracts are never actually deliverable for
        retail clients, so a position left open into the close-out window
        would, in real trading, simply be liquidated by the broker anyway.
        No-op if there's nothing open to close.
        """
        self._force_close(
            config=config,
            instrument=instrument,
            current_position_qty=current_position_qty,
            avg_entry_price=avg_entry_price,
            paper_position_id=paper_position_id,
            label=label,
            event_type="contract_expiry_close_out",
            log_reason_phrase="EXPIRY CLOSE-OUT (contract nearing MCX Tender Period -- Dhan does "
            "not permit physical delivery for retail clients)",
        )

    def force_close_end_of_day(
        self,
        config: Any,
        instrument: Any,
        current_position_qty: float,
        avg_entry_price: Optional[float],
        paper_position_id: Optional[uuid.UUID],
        label: str = "",
    ) -> None:
        """Force-close an open position near the daily MCX session close,
        for a strategy whose logic is inherently single-day (see
        `Strategy.requires_intraday_flatten`). Mirrors
        `LiveTradingEngine.force_close_end_of_day` for paper-trading
        realism, even though a simulated position carries no real risk if
        left open. No-op if there's nothing open to close.
        """
        self._force_close(
            config=config,
            instrument=instrument,
            current_position_qty=current_position_qty,
            avg_entry_price=avg_entry_price,
            paper_position_id=paper_position_id,
            label=label,
            event_type="position_force_closed_end_of_day",
            log_reason_phrase="END-OF-DAY FLATTEN (single-day strategy)",
        )

    def _force_close(
        self,
        config: Any,
        instrument: Any,
        current_position_qty: float,
        avg_entry_price: Optional[float],
        paper_position_id: Optional[uuid.UUID],
        label: str,
        event_type: str,
        log_reason_phrase: str,
    ) -> None:
        if current_position_qty <= 0 or paper_position_id is None or avg_entry_price is None:
            return

        now = datetime.now(timezone.utc)
        quote = self.dhan_client.get_quote(instrument)
        pnl = (float(quote.ltp) - float(avg_entry_price)) * current_position_qty * instrument.lot_size

        position = self.session.get(PaperPosition, paper_position_id)
        position.realized_pnl = float(position.realized_pnl or 0) + pnl
        position.quantity = 0
        position.status = "closed"
        position.closed_at = now
        self._mark_to_market(position, quote.ltp, instrument.lot_size)
        self.session.add(position)

        logger.warning(
            "%s -- %s qty=%s price=%s pnl=%.2f",
            label or paper_position_id,
            log_reason_phrase,
            current_position_qty,
            quote.ltp,
            pnl,
        )

        self.session.add(
            PaperOrder(
                id=uuid.uuid4(),
                paper_position_id=paper_position_id,
                side="sell",
                quantity=current_position_qty,
                simulated_fill_price=quote.ltp,
                filled_at=now,
                pnl=pnl,
            )
        )
        self.session.add(
            AuditLog(
                id=uuid.uuid4(),
                ts=now,
                event_type=event_type,
                payload={
                    "strategy_id": str(config.strategy_id),
                    "instrument_id": str(config.instrument_id),
                    "quantity": current_position_qty,
                    "pnl": pnl,
                },
            )
        )

    def _trip_daily_loss_guard(
        self,
        config: Any,
        instrument: Any,
        now: datetime,
        cumulative_daily_pnl: float,
        current_position_qty: float = 0.0,
        avg_entry_price: Optional[float] = None,
        paper_position_id: Optional[uuid.UUID] = None,
        label: str = "",
    ) -> None:
        config.enabled = False

        auto_close_attempted = False
        auto_close_succeeded = False
        close_pnl: Optional[float] = None

        has_open_position = (
            current_position_qty > 0 and paper_position_id is not None and avg_entry_price is not None
        )
        if has_open_position:
            auto_close_attempted = True
            try:
                quote = self.dhan_client.get_quote(instrument)
                close_pnl = (
                    (float(quote.ltp) - float(avg_entry_price)) * current_position_qty
                    * instrument.lot_size
                )
                position = self.session.get(PaperPosition, paper_position_id)
                position.realized_pnl = float(position.realized_pnl or 0) + close_pnl
                position.quantity = 0
                position.status = "closed"
                position.closed_at = now
                self._mark_to_market(position, quote.ltp, instrument.lot_size)
                self.session.add(position)
                self.session.add(
                    PaperOrder(
                        id=uuid.uuid4(),
                        paper_position_id=paper_position_id,
                        side="sell",
                        quantity=current_position_qty,
                        simulated_fill_price=quote.ltp,
                        filled_at=now,
                        pnl=close_pnl,
                    )
                )
                auto_close_succeeded = True
                logger.warning(
                    "%s -- DAILY LOSS LIMIT TRIPPED, auto-closed the open position "
                    "qty=%s pnl=%.2f",
                    label or config.strategy_id,
                    current_position_qty,
                    close_pnl,
                )
            except Exception:
                logger.exception(
                    "%s -- DAILY LOSS LIMIT TRIPPED but auto-close failed -- position remains "
                    "open, needs manual review",
                    label or config.strategy_id,
                )
        else:
            logger.warning(
                "%s -- DAILY LOSS LIMIT TRIPPED, disabling (cumulative_daily_pnl=%.2f, "
                "daily_loss_limit=%.2f)",
                label or config.strategy_id,
                cumulative_daily_pnl,
                float(config.daily_loss_limit),
            )

        self.session.add(
            AuditLog(
                id=uuid.uuid4(),
                ts=now,
                event_type="risk_guard_daily_loss_limit_tripped",
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
        paper_position_id: Optional[uuid.UUID] = None,
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
            if _should_log_max_position_rejection(self.session, config.id, now):
                self.session.add(
                    AuditLog(
                        id=uuid.uuid4(),
                        ts=now,
                        event_type="risk_guard_max_position_size_rejected",
                        payload={
                            "strategy_id": str(config.strategy_id),
                            "instrument_id": str(config.instrument_id),
                            "requested_total": new_total,
                            "max_position_size": float(config.max_position_size),
                        },
                    )
                )
            return

        logger.info(
            "%s -- BUY FILLED qty=%s price=%s %s",
            label or config.strategy_id,
            size,
            quote.ltp,
            computed,
        )

        if current_position_qty == 0 or paper_position_id is None:
            position_id = uuid.uuid4()
            self.session.add(
                PaperPosition(
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
            # Adding to an existing position -- blend the average entry price
            # over the combined quantity rather than leaving the position's
            # own quantity/avg_entry_price stale (a real gap found while
            # first wiring up real paper trading).
            position_id = paper_position_id
            position = self.session.get(PaperPosition, paper_position_id)
            blended_avg = (
                float(position.avg_entry_price) * current_position_qty + float(quote.ltp) * size
            ) / new_total
            position.avg_entry_price = blended_avg
            position.quantity = new_total
            self._mark_to_market(position, quote.ltp, instrument.lot_size)
            self.session.add(position)

        self.session.add(
            PaperOrder(
                id=uuid.uuid4(),
                paper_position_id=position_id,
                side="buy",
                quantity=size,
                simulated_fill_price=quote.ltp,
                filled_at=now,
            )
        )

    def _handle_sell(
        self,
        instrument: Any,
        current_position_qty: float,
        avg_entry_price: Optional[float],
        paper_position_id: Optional[uuid.UUID],
        quote: Any,
        size: float,
        now: datetime,
        label: str = "",
        computed: str = "",
    ) -> None:
        if current_position_qty <= 0 or paper_position_id is None or avg_entry_price is None:
            return  # Nothing open to close -- no shorting.

        close_qty = min(size, current_position_qty)
        remaining_qty = current_position_qty - close_qty
        # Real rupee P&L needs the instrument's real lot size (e.g. Gold
        # Mini=100g) -- `close_qty` itself stays in lot units.
        pnl = (float(quote.ltp) - float(avg_entry_price)) * close_qty * instrument.lot_size

        position = self.session.get(PaperPosition, paper_position_id)
        position.realized_pnl = float(position.realized_pnl or 0) + pnl
        position.quantity = remaining_qty
        if remaining_qty <= 0:
            position.status = "closed"
            position.closed_at = now
        self._mark_to_market(position, quote.ltp, instrument.lot_size)
        self.session.add(position)

        logger.info(
            "%s -- SELL FILLED qty=%s price=%s pnl=%.2f %s %s",
            label or paper_position_id,
            close_qty,
            quote.ltp,
            pnl,
            "(position closed)" if remaining_qty <= 0 else f"(remaining_qty={remaining_qty})",
            computed,
        )

        self.session.add(
            PaperOrder(
                id=uuid.uuid4(),
                paper_position_id=paper_position_id,
                side="sell",
                quantity=close_qty,
                simulated_fill_price=quote.ltp,
                filled_at=now,
                pnl=pnl,
            )
        )


__all__ = ["PaperTradingEngine"]
