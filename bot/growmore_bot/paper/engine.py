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
from datetime import datetime, timezone
from typing import Any, Optional

from growmore_bot.persistence.models import AuditLog, PaperOrder, PaperPosition
from growmore_bot.strategies.base import SignalAction, Strategy

logger = logging.getLogger(__name__)


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
    ) -> None:
        if not config.enabled:
            return

        now = datetime.now(timezone.utc)

        if cumulative_daily_pnl <= -float(config.daily_loss_limit):
            self._trip_daily_loss_guard(config, now, cumulative_daily_pnl)
            return

        quote = self.dhan_client.get_quote(instrument)

        position_state = (
            None
            if current_position_qty == 0
            else {"quantity": current_position_qty, "avg_entry_price": avg_entry_price}
        )
        signal = strategy.on_bar(quote, position_state)

        if signal.action == SignalAction.HOLD:
            # INFO, not DEBUG: fires once per tick per enabled config (every
            # 5 minutes by default), so it's a cheap, useful "still alive and
            # checking" signal in bot.log, not log spam.
            logger.info(
                "HOLD -- strategy_id=%s instrument_id=%s ltp=%s",
                config.strategy_id,
                config.instrument_id,
                quote.ltp,
            )
            return

        size = signal.size or 1

        if signal.action == SignalAction.BUY:
            self._handle_buy(
                config, instrument, quote, current_position_qty, size, now, paper_position_id
            )
        elif signal.action == SignalAction.SELL:
            self._handle_sell(
                instrument, current_position_qty, avg_entry_price, paper_position_id, quote, size, now
            )

    def _trip_daily_loss_guard(self, config: Any, now: datetime, cumulative_daily_pnl: float) -> None:
        config.enabled = False
        logger.warning(
            "Daily loss limit tripped -- disabling strategy_id=%s instrument_id=%s "
            "(cumulative_daily_pnl=%.2f, daily_loss_limit=%.2f)",
            config.strategy_id,
            config.instrument_id,
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
        paper_position_id: Optional[uuid.UUID] = None,
    ) -> None:
        new_total = current_position_qty + size
        if new_total > float(config.max_position_size):
            logger.warning(
                "BUY signal REJECTED (max_position_size) -- strategy_id=%s instrument_id=%s "
                "requested_total=%s max_position_size=%s",
                config.strategy_id,
                config.instrument_id,
                new_total,
                float(config.max_position_size),
            )
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
            "BUY signal filled -- strategy_id=%s instrument_id=%s qty=%s price=%s",
            config.strategy_id,
            config.instrument_id,
            size,
            quote.ltp,
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
            position.unrealized_pnl = 0
        self.session.add(position)

        logger.info(
            "SELL signal filled -- position_id=%s qty=%s price=%s pnl=%.2f %s",
            paper_position_id,
            close_qty,
            quote.ltp,
            pnl,
            "(position closed)" if remaining_qty <= 0 else f"(remaining_qty={remaining_qty})",
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
