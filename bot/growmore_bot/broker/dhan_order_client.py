"""The ONLY module in this codebase allowed to call Dhan's real Order API.

Hard rules (see CLAUDE.md non-negotiables):
  1. Every call requires `live_trading_enabled=True`, checked at call time
     (not just construction) -- refuses even if some caller holds a stale
     instance across a flag flip.
  2. Every attempted call -- whether it succeeds or Dhan's API rejects it --
     is written to `audit_log` before returning or raising.

`growmore_bot.broker.dhan_client.DhanClient` (Data API only) is a completely
separate class with its own hard runtime allow-list (`_SafeSdk`) blocking
`place_order` -- it must never be extended to reach this code path. This
class is deliberately narrow: it only knows how to place one thing, a real
MARKET order, nothing else (no modify/cancel/other segments).

Order schema confirmed against the installed `dhanhq==2.2.0` SDK source
(2026-09-04), not guessed:
  - `exchange_segment` for MCX commodities is the SDK's own `dhanhq.MCX`
    constant, `"MCX_COMM"` -- matches what growmore_bot.broker.dhan_client
    already uses for quotes (a scraped doc page claimed "MCX_FO", which the
    SDK source directly contradicts -- trust the SDK, not the doc page).
  - `product_type="MARGIN"` (carry-forward, Dhan's NRML-equivalent) is used
    deliberately instead of `"INTRADAY"` (Dhan's MIS-equivalent, which
    auto-square-offs the position same day) -- every strategy this bot runs
    holds positions across multiple days, so INTRADAY would silently force
    an unintended exit every single day regardless of the strategy's own
    signal.
  - `order_type="MARKET"`, `price=0` (Dhan ignores price for market orders).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from dhanhq import DhanContext
from dhanhq import dhanhq as _DhanSdk

logger = logging.getLogger(__name__)

_VALID_TRANSACTION_TYPES = frozenset({"BUY", "SELL"})


@dataclass(frozen=True)
class PlacedOrder:
    order_id: str
    order_status: str


class LiveTradingDisabledError(RuntimeError):
    """Raised when a real order is attempted while the live-trading kill
    switch (Settings().live_trading_enabled) is off."""


class DhanOrderError(RuntimeError):
    """Raised when Dhan's Order API responds with a failure status."""


class DhanOrderClient:
    def __init__(
        self,
        client_id: str,
        access_token: str,
        live_trading_enabled: bool,
        session: Any,
    ) -> None:
        self._live_trading_enabled = live_trading_enabled
        self._session = session
        context = DhanContext(client_id, access_token)
        self._sdk = _DhanSdk(context)

    def place_market_order(
        self, instrument: Any, transaction_type: str, quantity: int
    ) -> PlacedOrder:
        """Place a real MARKET order on MCX for `instrument`.

        `quantity` is already lot-size-scaled real contract units (the
        caller's -- LiveTradingEngine's -- responsibility, same convention
        as PaperTradingEngine). Returns Dhan's real (orderId, orderStatus) on
        success -- a MARKET order's initial status (e.g. "TRANSIT") is not
        the same as a confirmed fill; see docs/technical-debt.md for the
        reconciliation gap this leaves.

        Raises LiveTradingDisabledError if the kill switch is off, ValueError
        for an invalid transaction_type, or DhanOrderError if Dhan's API
        reports failure. Every outcome is written to audit_log first.
        """
        from growmore_bot.persistence.models import AuditLog

        if not self._live_trading_enabled:
            raise LiveTradingDisabledError(
                "Refusing to place a real order: live trading is disabled "
                "(Settings().live_trading_enabled is False)."
            )
        if transaction_type not in _VALID_TRANSACTION_TYPES:
            raise ValueError(f"transaction_type must be BUY or SELL, got {transaction_type!r}")

        now = datetime.now(timezone.utc)
        audit_payload: dict[str, Any] = {
            "instrument_id": str(getattr(instrument, "id", instrument)),
            "symbol": instrument.symbol,
            "security_id": instrument.security_id,
            "transaction_type": transaction_type,
            "quantity": quantity,
        }

        response = self._sdk.place_order(
            security_id=instrument.security_id,
            exchange_segment=instrument.exchange_segment,
            transaction_type=transaction_type,
            quantity=quantity,
            order_type="MARKET",
            product_type="MARGIN",
            price=0,
        )

        if response.get("status") != "success":
            audit_payload["result"] = "failed"
            audit_payload["error"] = str(response.get("remarks"))
            self._session.add(
                AuditLog(
                    id=uuid.uuid4(), ts=now, event_type="live_order_failed", payload=audit_payload
                )
            )
            logger.error("LIVE ORDER FAILED (real money attempt): %s", audit_payload)
            raise DhanOrderError(str(response.get("remarks")))

        data = response.get("data") or {}
        order_id = data.get("orderId")
        order_status = data.get("orderStatus")
        audit_payload["result"] = "placed"
        audit_payload["broker_order_id"] = order_id
        audit_payload["order_status"] = order_status
        self._session.add(
            AuditLog(id=uuid.uuid4(), ts=now, event_type="live_order_placed", payload=audit_payload)
        )
        logger.warning("LIVE ORDER PLACED (REAL MONEY): %s", audit_payload)
        return PlacedOrder(order_id=order_id, order_status=order_status)

    def get_order_status(self, order_id: str) -> dict:
        """Read-only lookup of a real order's current status (GET
        /orders/{order_id}) -- used for fill reconciliation (see
        growmore_bot.live.engine.LiveTradingEngine.reconcile_pending_orders).
        Still gated behind live_trading_enabled (nothing meaningful to query
        when it's off) but never places, modifies, or cancels anything.

        Unlike place_market_order's request schema (verified against the
        dhanhq SDK's own source), the exact response field names here
        (`orderStatus`, `averageTradedPrice`, `filledQty` per Dhan's public
        docs) are NOT independently verified against a live response --
        callers must parse defensively.
        """
        if not self._live_trading_enabled:
            raise LiveTradingDisabledError(
                "Refusing to query order status: live trading is disabled "
                "(Settings().live_trading_enabled is False)."
            )
        return self._sdk.get_order_by_id(order_id)


__all__ = ["DhanOrderClient", "DhanOrderError", "LiveTradingDisabledError", "PlacedOrder"]
