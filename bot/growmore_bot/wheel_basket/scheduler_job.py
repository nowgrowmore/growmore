"""Orchestration wiring for the wheel-basket strategy's once-daily cycle --
the analog of growmore_bot.scheduler.run.run_all_enabled_configs, but for a
strategy that manages a rotating basket of many stocks under ONE config row
rather than one (strategy_id, instrument_id) pair.

Called from growmore_bot.scheduler.run.start(), gated by
growmore_bot.scheduler.nse_equity_hours.is_nse_trading_day and a separate
(much less frequent) CronTrigger than the 5-minute MCX tick job -- an
options wheel is decided at expiry, not intraday.
"""
from __future__ import annotations

import logging
from datetime import date
from types import SimpleNamespace
from typing import Any

from growmore_bot.persistence.models import WheelBasketConfig
from growmore_bot.wheel_basket.live_iv_rank import build_candidate
from growmore_bot.wheel_basket.universe import load_universe
from growmore_bot.wheel_basket.wheel_basket_engine import CandidateData, WheelBasketEngine

logger = logging.getLogger(__name__)


def run_wheel_basket_configs(session: Any, dhan_client: Any, today: date | None = None) -> None:
    configs = session.query(WheelBasketConfig).filter_by(enabled=True).all()
    if not configs:
        return

    cycle_date = today or date.today()
    candidates: dict[str, CandidateData] = {}
    for row in load_universe():
        instrument = SimpleNamespace(
            symbol=row.symbol, security_id=row.security_id,
            exchange_segment="NSE_FNO", lot_size=row.lot_size, instrument_type="OPTSTK",
        )
        try:
            expiries = dhan_client.get_expiry_list(instrument)
            if not expiries:
                continue
            expiry = expiries[0]
            candidate = build_candidate(
                dhan_client, instrument, expiry=expiry, cycle_expiry=date.fromisoformat(expiry),
            )
        except Exception:  # noqa: BLE001 -- one stock must not lose the whole cycle
            logger.exception("wheel_basket: failed to build candidate for %s", row.symbol)
            continue
        if candidate is not None:
            candidates[row.symbol] = candidate

    engine = WheelBasketEngine(session=session)
    for config in configs:
        engine.run_cycle(config, candidates, today=cycle_date)


__all__ = ["run_wheel_basket_configs"]
