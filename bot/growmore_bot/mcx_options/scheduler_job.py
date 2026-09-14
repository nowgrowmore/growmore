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
from datetime import date
from typing import Any, Optional

from growmore_bot.mcx_options import live_data
from growmore_bot.mcx_options.mcx_options_engine import run_cycle
from growmore_bot.persistence.models import Instrument, MCXOptionsConfig

logger = logging.getLogger(__name__)


def run_mcx_options_configs(session: Any, dhan_client: Any, today: Optional[date] = None) -> None:
    """Fetch every enabled `MCXOptionsConfig` row and run one daily cycle
    each: `live_data.fetch_cycle_data` builds that commodity's market data,
    then `mcx_options_engine.run_cycle` makes (and persists) the day's
    decision. No order placement anywhere in this path -- see
    `mcx_options_engine`'s own module docstring.
    """
    cycle_date = today or date.today()

    configs = session.query(MCXOptionsConfig).filter_by(enabled=True).all()
    for config in configs:
        instrument = session.query(Instrument).filter_by(symbol=config.symbol).one_or_none()
        if instrument is None:
            logger.warning(
                "mcx_options: no Instrument row for symbol=%s -- skipping this cycle",
                config.symbol,
            )
            continue
        try:
            cycle_data = live_data.fetch_cycle_data(dhan_client, instrument, cycle_date)
            run_cycle(session, config, cycle_data, today=cycle_date)
        except Exception:  # noqa: BLE001 -- one commodity must not lose the whole cycle
            logger.exception(
                "mcx_options: cycle failed for symbol=%s -- skipping this commodity today",
                config.symbol,
            )
            continue


__all__ = ["run_mcx_options_configs"]
