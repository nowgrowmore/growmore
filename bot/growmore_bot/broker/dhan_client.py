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
            vwap=float(raw_vwap) if raw_vwap is not None else None,
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
        return [
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
