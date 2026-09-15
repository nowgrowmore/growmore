"""Orchestration wiring for the MCX options-selling strategy's once-daily
cycle -- the MCX-options analog of
`growmore_bot.wheel_basket.scheduler_job.run_wheel_basket_configs`.

Called from `growmore_bot.scheduler.run.start()`'s `_mcx_options_job`, gated
by `growmore_bot.scheduler.market_hours.is_mcx_trading_day` and a separate,
once-a-day `CronTrigger` (see `run.py` for the exact time and why).

Each `MCXOptionsConfig` row is one commodity (GOLDM/SILVERM); a per-config
try/except (mirroring `run_all_enabled_configs`'s own exception-swallow-
and-log-and-continue discipline) means one commodity's Dhan/data failure
never aborts another commodity's cycle for the day.
"""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any, Optional

from growmore_bot.broker.dhan_client import DhanApiError
from growmore_bot.broker.instrument_master import fetch_instrument_master_csv
from growmore_bot.mcx_options import live_data
from growmore_bot.mcx_options.mcx_options_engine import run_cycle
from growmore_bot.persistence.models import Instrument, MCXOptionsConfig
from growmore_bot.scheduler.contract_rollover import (
    is_past_close_out_cutoff,
    roll_to_next_contract,
)

logger = logging.getLogger(__name__)

#: Confirmed 2026-09-15 production incident: calling `fetch_cycle_data` for
#: GOLDM then IMMEDIATELY for SILVERM (back-to-back, no delay -- exactly what
#: this loop used to do) made SILVERM's `get_quote` call fail with an opaque,
#: no-detail `DhanApiError`, while retrying the SAME call in isolation a few
#: seconds later succeeded immediately. That strongly suggests Dhan enforces
#: a short burst/rate-limit window across calls. This job runs once a day, so
#: a couple of seconds' pause between commodities costs nothing operationally
#: while giving any such per-second-scale limit time to clear before the next
#: commodity's four Dhan calls begin.
_INTER_CONFIG_DELAY_SECONDS = 2.0

#: Backoff between retry attempts of one commodity's `fetch_cycle_data` call
#: after a `DhanApiError`. Same reasoning as the inter-config delay above --
#: a short burst-limit window -- but given a bit more margin than the
#: inter-config gap since this is the fallback for when spacing out configs
#: alone wasn't enough.
_FETCH_RETRY_DELAY_SECONDS = 2.5

#: Initial attempt plus up to 2 retries. A persistent failure (not just a
#: transient burst-limit trip) should still surface quickly rather than
#: holding up the day's cycle for other commodities indefinitely -- the
#: existing per-config try/except in `run_mcx_options_configs` is the
#: backstop once these attempts are exhausted.
_FETCH_MAX_ATTEMPTS = 3


def _fetch_cycle_data_with_retry(
    dhan_client: Any, instrument: Any, cycle_date: date, config: Any = None
) -> Any:
    """Wraps `live_data.fetch_cycle_data` with a short retry-with-backoff on
    `DhanApiError` -- the exception type `DhanClient._raise_if_failed` raises
    for a failed Dhan response, including the opaque, all-None-detail error
    seen in the 2026-09-15 burst/rate-limit incident (see this module's
    `_FETCH_RETRY_DELAY_SECONDS` docstring above).

    Deliberately narrow: only `DhanApiError` is retried. Any other exception
    (e.g. a `ValueError`/`KeyError` from a genuine data-shape bug, or a
    `DhanTokenExpiredError`, which retrying can never fix) is left to
    propagate immediately to the caller's own per-config `except Exception`
    -- blindly retrying those would just mask a real bug behind extra delay.

    The retry policy lives here, in the scheduler, rather than in
    `live_data.fetch_cycle_data` itself, matching this module's own framing
    of `live_data` as "the ONLY module calling DhanClient, but a pure fetch,
    no retry policy baked in" -- retry/backoff is an orchestration concern.
    """
    # The days-to-expiry window is a MCXOptionsConfig preference (migration
    # 0027, default-OFF) but is applied during expiry SELECTION, inside
    # live_data -- so it has to be carried across from here.
    min_dte = getattr(config, "min_dte_days", None)
    max_dte = getattr(config, "max_dte_days", None)

    last_error: DhanApiError
    for attempt in range(1, _FETCH_MAX_ATTEMPTS + 1):
        try:
            return live_data.fetch_cycle_data(
                dhan_client,
                instrument,
                cycle_date,
                min_dte_days=None if min_dte is None else int(min_dte),
                max_dte_days=None if max_dte is None else int(max_dte),
            )
        except DhanApiError as exc:
            last_error = exc
            if attempt == _FETCH_MAX_ATTEMPTS:
                break
            logger.warning(
                "mcx_options: Dhan API call failed for symbol=%s (attempt %d/%d) -- "
                "retrying in %.1fs (likely a burst/rate-limit trip; see the "
                "2026-09-15 incident this retry was added for): %s",
                getattr(instrument, "symbol", instrument),
                attempt,
                _FETCH_MAX_ATTEMPTS,
                _FETCH_RETRY_DELAY_SECONDS,
                exc,
            )
            time.sleep(_FETCH_RETRY_DELAY_SECONDS)
    logger.warning(
        "mcx_options: Dhan API call still failing for symbol=%s after %d attempts -- "
        "giving up on this commodity's cycle today",
        getattr(instrument, "symbol", instrument),
        _FETCH_MAX_ATTEMPTS,
    )
    raise last_error


def _ensure_contract_is_current(
    session: Any, dhan_client: Any, instrument: Any, cycle_date: date
) -> bool:
    """Make sure `instrument` is not still pointing at a contract that is past
    its close-out cutoff, rolling it forward if it is. Returns True when the
    instrument is safe to trade this cycle.

    **Why this lives here at all** (found by independent code review,
    2026-09-15): `roll_to_next_contract` used to be reachable from exactly one
    place -- inside `scheduler.run.run_all_enabled_configs`'s loop over
    `BotConfig.enabled == True`. This strategy is configured exclusively
    through `mcx_options_configs` and never creates a `bot_config` row, so
    unless a commodity ALSO happened to have a separate enabled futures
    `BotConfig`, its `Instrument.contract_expiry`/`security_id` never
    advanced. Two consequences, both silent:

      1. `live_data.fetch_cycle_data` kept requesting quotes, historical bars
         and option chains for an EXPIRED contract's `security_id`;
      2. `mcx_options_engine.run_cycle`'s own futures-rollover detection
         compares a position's `futures_contract_expiry` against that same
         Instrument field, so it could never become true --
         `_roll_futures_position` was effectively dead code in production.

    Rolling here (rather than extending the 5-minute tick's instrument set)
    keeps the dependency pointing the same direction the rest of this module
    already does, and means the roll happens immediately before the cycle that
    needs it.

    **Failing closed is deliberate.** If the contract is past cutoff and the
    roll does not succeed -- the instrument master is unreachable, or
    `roll_to_next_contract` refuses to guess the next contract -- this returns
    False and the caller skips the commodity for the day. Trading an expired
    contract's `security_id` is strictly worse than missing a cycle: every
    quote, strike selection and settlement decision would be made against a
    contract that no longer exists. The existing manual rollover process
    (docs/pending-actions.md) remains the fallback, exactly as it is for the
    futures tick.
    """
    if not is_past_close_out_cutoff(
        instrument.symbol, instrument.contract_expiry, cycle_date
    ):
        return True

    logger.warning(
        "mcx_options: %s's contract (expiry %s) is past its close-out cutoff -- attempting "
        "an automatic rollover before running today's cycle",
        instrument.symbol,
        instrument.contract_expiry,
    )
    try:
        csv_text = fetch_instrument_master_csv()
        rolled = roll_to_next_contract(session, dhan_client, instrument, csv_text)
    except Exception:  # noqa: BLE001 -- see this function's docstring: fail closed
        logger.exception(
            "mcx_options: automatic rollover attempt failed for %s -- skipping this "
            "commodity's cycle today rather than trading an expired contract",
            instrument.symbol,
        )
        return False

    if not rolled:
        logger.warning(
            "mcx_options: could not roll %s to its next contract -- skipping this "
            "commodity's cycle today rather than trading an expired contract. Manual "
            "rollover (see docs/pending-actions.md) is still available.",
            instrument.symbol,
        )
        return False

    logger.info(
        "mcx_options: rolled %s to its next contract (expiry now %s) -- proceeding with "
        "today's cycle",
        instrument.symbol,
        instrument.contract_expiry,
    )
    return True


def run_mcx_options_configs(session: Any, dhan_client: Any, today: Optional[date] = None) -> None:
    """Fetch every enabled `MCXOptionsConfig` row and run one daily cycle
    each: `live_data.fetch_cycle_data` builds that commodity's market data,
    then `mcx_options_engine.run_cycle` makes (and persists) the day's
    decision. No order placement anywhere in this path -- see
    `mcx_options_engine`'s own module docstring.
    """
    cycle_date = today or date.today()

    configs = session.query(MCXOptionsConfig).filter_by(enabled=True).all()
    for index, config in enumerate(configs):
        # Space out each commodity's Dhan calls -- see `_INTER_CONFIG_DELAY_
        # SECONDS`'s docstring above for why. No delay before the first
        # config; nothing to space out yet.
        if index > 0:
            time.sleep(_INTER_CONFIG_DELAY_SECONDS)

        instrument = session.query(Instrument).filter_by(symbol=config.symbol).one_or_none()
        if instrument is None:
            logger.warning(
                "mcx_options: no Instrument row for symbol=%s -- skipping this cycle",
                config.symbol,
            )
            continue
        try:
            # Never trade an expired contract -- see _ensure_contract_is_current.
            if not _ensure_contract_is_current(session, dhan_client, instrument, cycle_date):
                continue
            cycle_data = _fetch_cycle_data_with_retry(
                dhan_client, instrument, cycle_date, config
            )
            run_cycle(session, config, cycle_data, today=cycle_date)
        except Exception:  # noqa: BLE001 -- one commodity must not lose the whole cycle
            logger.exception(
                "mcx_options: cycle failed for symbol=%s -- skipping this commodity today",
                config.symbol,
            )
            continue


__all__ = ["run_mcx_options_configs"]
