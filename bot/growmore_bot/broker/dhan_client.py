"""Thin wrapper around the `dhanhq` SDK -- Data API only.

Hard rule (see CLAUDE.md / docs/architecture.md): this module must NEVER call,
or even expose, an order-placement method from the underlying SDK. `_SafeSdk`
enforces this at runtime by allow-listing the handful of Data API methods we
actually use; any other attribute access (place_order, modify_order,
cancel_order, place_slice_order, ...) raises AttributeError.

Token refresh: Dhan access tokens are JWTs with a standard `exp` claim, but
Dhan does not offer a fully-automated re-authentication flow -- getting a new
token requires the account owner to regenerate it manually via the Dhan web
console. So `refresh_access_token_if_needed()` deliberately does NOT attempt
to auto-refresh; it just decodes the `exp` claim and raises a clear,
actionable error once the token is expired (or about to expire).
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from dhanhq import DhanContext
from dhanhq import dhanhq as _DhanSdk

# The only Data API methods this wrapper is allowed to call. Deliberately does
# NOT include place_order / place_slice_order / modify_order / cancel_order /
# place_forever / place_super_order / modify_forever / modify_super_order /
# cancel_forever / cancel_super_order / kill_switch, etc.
_ALLOWED_SDK_METHODS = frozenset(
    {
        "quote_data",
        "ohlc_data",
        "ticker_data",
        "historical_daily_data",
        "intraday_minute_data",
        # Account-info read (GET /fundlimit) -- balance/margin only, never
        # mutates anything. Added 2026-09-04 for the dashboard's fund
        # balance display; still no order-placement capability whatsoever.
        "get_fund_limits",
    }
)


class DhanApiError(RuntimeError):
    """Raised when the Dhan API responds with a failure status."""


class DhanTokenExpiredError(RuntimeError):
    """Raised when the configured Dhan access token is expired (or about to be).

    Dhan does not support unattended token refresh -- the account owner must
    regenerate the access token from the Dhan web console and update
    DHAN_ACCESS_TOKEN (in .env.local) themselves.
    """


@dataclass(frozen=True)
class Quote:
    ltp: float
    open: float
    high: float
    low: float
    close: float
    # Cumulative volume traded today, and today's real session VWAP -- both
    # confirmed present in Dhan's real quote response (`volume` and
    # `average_price` fields respectively, 2026-09-04) but previously
    # unparsed. `vwap` is None (not 0.0) when genuinely absent from the
    # response -- a strategy comparing price to a VWAP of 0 would produce a
    # nonsensical always-above signal instead of correctly holding off.
    volume: float = 0.0
    vwap: Optional[float] = None


@dataclass(frozen=True)
class FundLimits:
    # Field names match Dhan's own real response verbatim (including their
    # "availabelBalance" typo) at the API boundary; this dataclass exposes
    # correctly-spelled attribute names instead.
    available_balance: float
    utilized_amount: float
    withdrawable_balance: float


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


logger = logging.getLogger(__name__)


class _SafeSdk:
    """Proxy that only forwards attribute access for allow-listed Data API methods."""

    def __init__(self, sdk: Any) -> None:
        self._sdk = sdk

    def __getattr__(self, name: str) -> Any:
        if name not in _ALLOWED_SDK_METHODS:
            raise AttributeError(
                f"'{name}' is not a permitted Data API method on DhanClient "
                "(order-placement / account-mutating methods are intentionally "
                "blocked -- this bot is paper-trading only)"
            )
        return getattr(self._sdk, name)


def _decode_jwt_exp(token: str) -> Optional[datetime]:
    """Best-effort, unverified decode of a JWT's `exp` claim.

    We don't hold Dhan's signing key and don't need to verify the signature --
    we only need the expiry to warn the operator in time. Returns None if the
    token isn't a parseable JWT.
    """
    try:
        _, payload_b64, _ = token.split(".")
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        exp = payload.get("exp")
        if exp is None:
            return None
        return datetime.fromtimestamp(exp, tz=timezone.utc)
    except Exception:
        return None


def _validated_bars(bars: list[Bar], symbol: Any) -> list[Bar]:
    """Drop bars Dhan reports that cannot be real prices, and collapse
    duplicate timestamps.

    Found 2026-09-05: Dhan returns NICKEL bars with open=high=low=0.0
    alongside a real close and a real volume -- 5 of 1,252 over a 5-year
    window, plus a duplicated date. Those zeros are missing fields, not
    prices, and unfiltered they corrupt everything computed from a bar's
    range: a Donchian channel low of 0, a Bollinger band against a 100%
    "move", an ATR inflated by a 1,873-point true range, and -- once
    ATR-based stops existed -- a stop at a NEGATIVE price which then
    "filled" and booked a Rs 485,199 loss on one trade, producing a 199%
    max drawdown on a long-only 1x position. That impossibility is what
    surfaced it; before stops existed the same bad bars were silently
    skewing every NICKEL result instead.

    Dropped rather than repaired: reconstructing open=high=low=close would
    invent a zero-range bar, which quietly deflates ATR and flatters any
    range-based indicator. Five missing days in five years is the smaller
    distortion, and it is a visible one.
    """
    def _usable(bar: Bar) -> bool:
        if min(bar.open, bar.high, bar.low, bar.close) <= 0:
            return False
        if bar.high < bar.low or bar.high < bar.open or bar.high < bar.close:
            return False
        return not (bar.low > bar.open or bar.low > bar.close)

    dropped_bad = sum(1 for bar in bars if not _usable(bar))
    usable = [bar for bar in bars if _usable(bar)]

    # Same-timestamp bars are NOT redundant copies. Dhan's "5-year" daily
    # series for one security_id overlaps two CONTRACT MONTHS around every
    # roll: for GOLDM, 41 of 43 repeated dates carry different OHLC and
    # different volume -- e.g. 2022-10-09 returns one bar on 5,603 lots and
    # another on 15,000, about 1% apart in price. The high-volume one is the
    # liquid front month; the other is the expiring contract nobody is
    # trading. Keeping whichever arrived first (an earlier version of this
    # function did) silently picks the illiquid contract roughly half the
    # time, injecting a fake ~1% gap at every roll.
    by_timestamp: dict = {}
    dropped_dupe = 0
    for bar in usable:
        existing = by_timestamp.get(bar.timestamp)
        if existing is None:
            by_timestamp[bar.timestamp] = bar
            continue
        dropped_dupe += 1
        if bar.volume > existing.volume:
            by_timestamp[bar.timestamp] = bar
    clean = sorted(by_timestamp.values(), key=lambda b: b.timestamp)

    if dropped_bad or dropped_dupe:
        logger.warning(
            "%s: dropped %d unusable historical bar(s) and resolved %d repeated timestamp(s) "
            "to the higher-volume contract, out of %d returned by Dhan",
            symbol, dropped_bad, dropped_dupe, len(bars),
        )
    return clean


class DhanClient:
    """Data-API-only wrapper around dhanhq's `dhanhq` client."""

    def __init__(self, client_id: str, access_token: str) -> None:
        self._client_id = client_id
        self._access_token = access_token
        context = DhanContext(client_id, access_token)
        self._sdk = _SafeSdk(_DhanSdk(context))

    def refresh_access_token_if_needed(self) -> None:
        exp = _decode_jwt_exp(self._access_token)
        if exp is None:
            # Can't determine expiry (e.g. sandbox token in a non-JWT shape) --
            # nothing we can safely automate, so just proceed.
            return
        if datetime.now(timezone.utc) >= exp:
            raise DhanTokenExpiredError(
                "Dhan access token has expired. Dhan does not support automated "
                "re-authentication -- regenerate a new access token from the Dhan "
                "web console and update DHAN_ACCESS_TOKEN in .env.local."
            )

    def get_quote(self, instrument: Any) -> Quote:
        # Dhan's batched marketfeed endpoints (quote/ohlc/ltp) require security
        # IDs as integers in the request payload, even though `security_id` is
        # a str everywhere else (it's a str key in the JSON *response*, and in
        # our own Instrument/CommodityPlaceholder models). Confirmed against
        # the real API 2026-09-03 -- passing it as a string is silently
        # rejected (non-2xx with no errorCode/errorType in the body, which
        # `_raise_if_failed` surfaces as an all-None DhanApiError).
        response = self._sdk.quote_data(
            {instrument.exchange_segment: [int(instrument.security_id)]}
        )
        self._raise_if_failed(response)
        # dhanhq wraps the raw HTTP body under response["data"]; Dhan's own
        # quote/ohlc/ltp response bodies are themselves {"status":..., "data": {...}}.
        body = response["data"]
        quote_payload = body.get("data", body)
        segment_data = quote_payload[instrument.exchange_segment][instrument.security_id]
        ohlc = segment_data.get("ohlc", {})
        raw_vwap = segment_data.get("average_price")
        return Quote(
            ltp=float(segment_data["last_price"]),
            open=float(ohlc.get("open", 0)),
            high=float(ohlc.get("high", 0)),
            low=float(ohlc.get("low", 0)),
            close=float(ohlc.get("close", 0)),
            volume=float(segment_data.get("volume", 0) or 0),
            # A real `average_price: 0` (not a missing key) means "no trades
            # printed yet this session" -- treated the same as genuinely
            # absent, not a real VWAP of zero. Found via independent code
            # review 2026-09-04: without this, VwapSessionBounceStrategy's
            # `ltp > vwap` was unconditionally True against a VWAP of 0.0,
            # fabricating a crossing (and a BUY/SELL) the instant real
            # trades start printing and vwap jumps to its first real value.
            vwap=float(raw_vwap) if raw_vwap else None,
        )

    def get_historical_ohlc(
        self,
        instrument: Any,
        from_date: str,
        to_date: str,
        interval: Literal["day"] | int = "day",
    ) -> list[Bar]:
        instrument_type = getattr(instrument, "instrument_type", "FUTCOM")
        if interval == "day":
            response = self._sdk.historical_daily_data(
                security_id=instrument.security_id,
                exchange_segment=instrument.exchange_segment,
                instrument_type=instrument_type,
                from_date=from_date,
                to_date=to_date,
            )
        else:
            response = self._sdk.intraday_minute_data(
                security_id=instrument.security_id,
                exchange_segment=instrument.exchange_segment,
                instrument_type=instrument_type,
                from_date=from_date,
                to_date=to_date,
                interval=interval,
            )
        self._raise_if_failed(response)
        data = response["data"] if "data" in response else response
        timestamps = data.get("timestamp", [])
        raw = [
            Bar(
                timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
                open=float(data["open"][i]),
                high=float(data["high"][i]),
                low=float(data["low"][i]),
                close=float(data["close"][i]),
                volume=float(data.get("volume", [0] * len(timestamps))[i]),
            )
            for i, ts in enumerate(timestamps)
        ]
        return _validated_bars(raw, getattr(instrument, "symbol", instrument))

    def get_fund_limits(self) -> FundLimits:
        """Real account balance/margin (GET /fundlimit) -- read-only, no
        order-placement capability whatsoever. Field names confirmed against
        a real response 2026-09-04 (including Dhan's own "availabelBalance"
        typo at the wire boundary).
        """
        response = self._sdk.get_fund_limits()
        self._raise_if_failed(response)
        data = response["data"] if "data" in response else response
        return FundLimits(
            available_balance=float(data["availabelBalance"]),
            utilized_amount=float(data["utilizedAmount"]),
            withdrawable_balance=float(data["withdrawableBalance"]),
        )

    @staticmethod
    def _raise_if_failed(response: dict) -> None:
        # Real dhanhq responses use {"status": "success"/"failure", "data":...,
        # "remarks":...}; our tests mock the raw HTTP body directly (a plain
        # dict of OHLC arrays or a Dhan error body), so treat any response
        # carrying an explicit failure status or an errorCode as a failure.
        status = response.get("status") if isinstance(response, dict) else None
        if status == "failure":
            raise DhanApiError(str(response.get("remarks")))
        if isinstance(response, dict) and "errorCode" in response:
            raise DhanApiError(
                f"{response.get('errorCode')}: {response.get('errorMessage')}"
            )


__all__ = ["DhanClient", "DhanApiError", "DhanTokenExpiredError", "Quote", "Bar", "FundLimits"]
