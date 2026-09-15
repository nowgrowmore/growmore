"""Builds one commodity's live `MCXCycleData` from Dhan's real-time option
chain, expiry list, live quote, and historical daily futures bars.

The ONLY module in `growmore_bot/mcx_options/` allowed to call `DhanClient`
methods -- live analog of `growmore_bot/wheel_basket/live_iv_rank.py`'s role
for wheel_basket. `mcx_options_engine.py`'s decision logic never calls Dhan
directly; it only ever sees the plain `MCXCycleData` this module produces,
which keeps the decision logic unit-testable without any Dhan mock plumbing
inside it.

**Confirmed historical-endpoint lag (2026-09-15 production incident) --
why `futures_price` comes from a live quote, not a historical bar**: a live
diagnostic against Dhan's real API on the production VPS confirmed
`get_historical_ohlc(..., to_date=today)` can lag several sessions behind
real time -- querying with `to_date=2026-09-15` returned bars only through
2026-09-13, and separately, three production cycles run hours apart on
2026-09-14 (19:13, 20:42, 23:59 IST) all recorded the identical
`futures_price=236895.0`, which turned out to match 2026-09-10's close --
i.e. even the *latest available* historical bar at decision time was
multiple days stale. `MCXCycleData.futures_price` feeds `mcx_options_engine
.py`'s strike/delta selection AND, most seriously, `_settle_leg`'s ITM/OTM
determination (`if F < leg.strike` for a put) -- a stale price there can
produce a WRONG assignment/expiry decision for a real (paper) position.
`futures_price` is therefore sourced from `dhan_client.get_quote(instrument)
.ltp` (last-traded price -- a genuinely real-time, "this actually just
happened" number), NOT from `bars[-1].close`. `close` fields on live quote
APIs can mean "previous session's close" rather than "today's close so
far" (Dhan's own `Quote.close` here is parsed straight from the marketfeed
response's `ohlc.close`, with no in-repo confirmation of which session it
reflects -- see `dhan_client.py`'s `get_quote`); `ltp` carries no such
ambiguity, so it is the defensible choice for a number a settlement
decision hangs on.

The historical daily bars (`get_historical_ohlc`) are still fetched and
returned as `futures_bars` -- `regime.classify_today`'s trailing ADX /
Bollinger-Bandwidth window only needs recent daily history, not today's
exact live price, and does not itself feed `_settle_leg`. A synthetic
"today" bar (built from the live quote) was deliberately NOT appended to
`futures_bars`: `classify_today` computes true range / directional
movement from each bar's `.high`/`.low` against the *previous* bar, and a
degenerate single-price bar (open=high=low=close=ltp) would flatten that
day's true range and directional movement to near-zero, distorting the
most recent ADX/+DI/-DI reading rather than improving it. A 1-2 day lag in
the regime read is a smaller, more tolerable staleness than using a stale
price for settlement, so `futures_bars` is left exactly as Dhan's
historical endpoint returns it.

**Unverified response shape, carried forward from dhan_client.py**:
`DhanClient.get_option_chain`/`get_expiry_list`'s response-shape parsing is
NOT yet verified against a real live Dhan call (see dhan_client.py's own
docstrings and docs/pending-actions.md) -- the same open risk
wheel_basket/live_iv_rank.py already lives with in production. This module
does not paper over that: a shape mismatch anywhere in Dhan's response
(a missing key, an unparseable expiry string) is left to raise a clear,
loud `KeyError`/`ValueError` rather than being caught and silently turned
into a wrong number.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from growmore_bot.broker.dhan_client import Bar, DhanClient, OptionChainSnapshot

#: Trailing window fed to the regime classifier's warm-up (ADX(14) +
#: Bollinger Bandwidth(20) + its 60-day percentile rank need
#: 20 + 60 - 1 = 79 bars minimum; comfortably over-fetched here the same way
#: wheel_basket/live_iv_rank.py over-fetches for its own indicator warm-up).
FUTURES_HISTORY_WARMUP_DAYS = 180

#: Minimum days-to-expiry for an option expiry to be considered tradeable at
#: all. Found by independent code review 2026-09-15: the old filter accepted
#: an expiry dated TODAY, which gives `T_years == 0`, and at T=0
#: `pricing.black76_delta` returns its BOUNDARY values -- 0.0 or +-1.0 for
#: every strike on the board. `select_strike_by_target_delta` then finds the
#: whole chain equidistant from the target delta and `min` breaks the tie by
#: returning the first entry in strike-ascending order: the deepest OTM
#: strike listed, worth approximately nothing. Worse, the leg written that
#: way carries `cycle_expiry == today`, and the settlement path would have to
#: see that same date again to resolve it.
#:
#: 1 day (i.e. strictly after today) is the minimum that makes the delta math
#: meaningful at all; it is deliberately NOT a strategy-level DTE preference
#: (see docs/pending-actions.md's proposed `min_dte_days`/`max_dte_days`
#: config columns for that -- this constant is a correctness floor, not a
#: tunable).
MIN_OPTION_DTE_DAYS = 1


@dataclass(frozen=True)
class MCXCycleData:
    """One commodity's market data for today's cycle decision -- everything
    `mcx_options_engine.run_cycle` needs, already fetched by this module.
    """

    #: Trailing daily bars from `get_historical_ohlc`, used ONLY for
    #: `regime.classify_today`'s trailing ADX/Bollinger-Bandwidth window --
    #: NOT the source of `futures_price` below (see this module's docstring
    #: for the confirmed multi-day lag on this endpoint). A 1-2 day lag here
    #: is comparatively tolerable since it only affects the trend read, not
    #: strike selection or settlement.
    futures_bars: tuple[Bar, ...]
    #: TODAY's/right-now's futures price -- sourced from a live quote
    #: (`dhan_client.get_quote(instrument).ltp`), never from a historical
    #: bar. Used for strike/delta selection AND `_settle_leg`'s ITM/OTM
    #: determination against real open positions; see this module's
    #: docstring for the confirmed incident that made a historical-bar
    #: source unsafe here.
    futures_price: float
    option_chain: OptionChainSnapshot
    option_expiry: date
    #: Years to `option_expiry` from `today`, floored at 0.
    T_years: float
    lot_size: int
    #: The underlying `Instrument`'s CURRENT `contract_expiry` as of this
    #: cycle -- kept fresh by `growmore_bot.scheduler.contract_rollover.
    #: roll_to_next_contract`, called earlier in the same tick (see
    #: `run.py`). `mcx_options_engine.run_cycle` compares this against a
    #: `long_futures` position's own `futures_contract_expiry` to detect
    #: when that mechanism has already rolled the Instrument's contract out
    #: from under an open futures position. `None` when the Instrument row
    #: itself has no `contract_expiry` recorded (purely informational field,
    #: nullable) -- rollover detection is then simply skipped, same as
    #: before this field existed.
    instrument_contract_expiry: date | None = None


def _parse_expiry(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(
            f"Could not parse Dhan expiry_list entry {raw!r} as an ISO date -- "
            "this likely means Dhan's expiry_list response shape does not match "
            "what DhanClient.get_expiry_list expects (see dhan_client.py's "
            "unverified-response-shape caveat)."
        ) from exc


def fetch_cycle_data(
    dhan_client: DhanClient,
    instrument: Any,
    today: date,
    min_dte_days: int | None = None,
    max_dte_days: int | None = None,
) -> MCXCycleData:
    """Fetch this cycle's futures history, live futures price, nearest
    upcoming option expiry, and that expiry's option chain for one
    commodity `instrument`.

    `futures_price` comes from a live quote (`dhan_client.get_quote(
    instrument).ltp`), NOT from the historical bars -- Dhan's historical
    daily-bar endpoint has a confirmed real lag (see this module's
    docstring), and `futures_price` is what `_settle_leg` uses to decide
    ITM/OTM assignment for real open positions. The historical bars are
    still fetched and returned as `futures_bars`, used only for the regime
    classifier's trailing window.

    Raises `ValueError` loudly (never returns a fabricated/partial result)
    if Dhan returns no historical bars at all, or no expiry at least
    `MIN_OPTION_DTE_DAYS` after `today` -- both real "can't decide today"
    outcomes the caller (`mcx_options_engine`) must not be handed silently
    as zeros.
    """
    from_date = today - timedelta(days=FUTURES_HISTORY_WARMUP_DAYS)
    bars = dhan_client.get_historical_ohlc(
        instrument, from_date=from_date.isoformat(), to_date=today.isoformat(), interval="day",
    )
    if not bars:
        raise ValueError(
            f"No futures history returned for {getattr(instrument, 'symbol', instrument)!r} "
            f"between {from_date.isoformat()} and {today.isoformat()}"
        )

    # `ltp`, not `close` -- see this module's docstring for why a live
    # quote's `close` field is not trustworthy as "today's price" here.
    futures_price = dhan_client.get_quote(instrument).ltp

    raw_expiries = dhan_client.get_expiry_list(instrument)
    parsed_expiries = [_parse_expiry(e) for e in raw_expiries]
    # Strictly in the future, by at least MIN_OPTION_DTE_DAYS -- an expiry
    # dated today is NOT tradeable here; see that constant's docstring.
    #
    # `min_dte_days`/`max_dte_days` (MCXOptionsConfig, migration 0027) narrow
    # that further to a strategy PREFERENCE, and are None/off by default. The
    # `max(...)` is deliberate: the config's floor can only ever tighten the
    # hard correctness floor, never undercut it, so setting `min_dte_days=0`
    # cannot re-admit a same-day expiry.
    floor_dte = MIN_OPTION_DTE_DAYS if min_dte_days is None else max(MIN_OPTION_DTE_DAYS, min_dte_days)
    upcoming = sorted(
        e
        for e in parsed_expiries
        if (e - today).days >= floor_dte
        and (max_dte_days is None or (e - today).days <= max_dte_days)
    )
    if not upcoming:
        raise ValueError(
            f"No tradeable option expiry (at least {floor_dte} day(s) after "
            f"{today.isoformat()}) returned by Dhan for "
            f"{getattr(instrument, 'symbol', instrument)!r} -- Dhan listed "
            f"{sorted(parsed_expiries)!r}"
            + (f", max {max_dte_days} day(s)" if max_dte_days is not None else "")
            + ". An expiry dated today is deliberately "
            "excluded (T_years would be 0, which collapses every Black-76 delta to a "
            "0/+-1 boundary and makes the target-delta strike pick meaningless)."
        )
    option_expiry = upcoming[0]

    chain = dhan_client.get_option_chain(instrument, expiry=option_expiry.isoformat())

    T_years = max((option_expiry - today).days, 0) / 365.25
    lot_size = int(getattr(instrument, "lot_size", 1))
    instrument_contract_expiry = getattr(instrument, "contract_expiry", None)

    return MCXCycleData(
        futures_bars=tuple(bars),
        futures_price=futures_price,
        option_chain=chain,
        option_expiry=option_expiry,
        T_years=T_years,
        lot_size=lot_size,
        instrument_contract_expiry=instrument_contract_expiry,
    )


__all__ = [
    "MCXCycleData",
    "FUTURES_HISTORY_WARMUP_DAYS",
    "MIN_OPTION_DTE_DAYS",
    "fetch_cycle_data",
]
