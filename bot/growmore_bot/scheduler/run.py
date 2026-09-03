"""Market-hours-aware scheduler for the paper trading engine.

Not a tick-driven daemon / not HFT: APScheduler polls on
`Settings().default_polling_interval_seconds` (default 5 minutes) and only
does anything when `is_market_open()` says MCX is trading.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Optional

from growmore_bot.scheduler.market_hours import MCX_TIMEZONE, is_market_open

logger = logging.getLogger(__name__)


def tick(run_all_configs: Callable[[], None], now: Optional[datetime] = None) -> None:
    """Scheduler callback: run `run_all_configs()` iff the market is open."""
    now = now or datetime.now(MCX_TIMEZONE)
    if not is_market_open(now):
        logger.debug("Market closed at %s, skipping tick", now.isoformat())
        return
    run_all_configs()


def _cumulative_daily_pnl(session: Any, strategy_id: Any, instrument_id: Any, now: datetime) -> float:
    """Sum today's (MCX-timezone calendar day) realized P&L for this
    strategy/instrument pair, from PaperOrder.pnl (set only on sell fills --
    see PaperTradingEngine._handle_sell). PaperPosition.realized_pnl is
    cumulative-ever, not per-day, so it can't answer "has today breached the
    daily loss limit" on its own.
    """
    from growmore_bot.persistence.models import PaperOrder, PaperPosition

    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = (
        session.query(PaperOrder.pnl)
        .join(PaperPosition, PaperOrder.paper_position_id == PaperPosition.id)
        .filter(
            PaperPosition.strategy_id == strategy_id,
            PaperPosition.instrument_id == instrument_id,
            PaperOrder.filled_at >= day_start,
            PaperOrder.pnl.isnot(None),
        )
        .all()
    )
    return sum(float(r[0]) for r in rows)


def run_all_enabled_configs(session: Any, dhan_client: Any, now: Optional[datetime] = None) -> None:
    """Fetch every enabled bot_config row and run one paper-trading tick each.

    Strategy instances are built fresh each tick from `strategies.name` +
    `strategies.params` -- this keeps the scheduler stateless between ticks
    (position state is read from `paper_positions`, not held in memory).
    """
    from growmore_bot.paper.engine import PaperTradingEngine
    from growmore_bot.persistence.models import BotConfig, Instrument, PaperPosition, Strategy
    from growmore_bot.strategies.bollinger_reversion import BollingerReversionStrategy
    from growmore_bot.strategies.donchian_breakout import DonchianBreakoutStrategy
    from growmore_bot.strategies.macd_trend import MacdTrendStrategy
    from growmore_bot.strategies.rsi_mean_reversion import RsiMeanReversionStrategy
    from growmore_bot.strategies.sma_crossover import SmaCrossoverStrategy

    now = now or datetime.now(MCX_TIMEZONE)

    strategy_builders: dict[str, Callable[[dict], Any]] = {
        "sma_crossover": lambda params: SmaCrossoverStrategy(**params),
        "donchian_breakout": lambda params: DonchianBreakoutStrategy(**params),
        "rsi_mean_reversion": lambda params: RsiMeanReversionStrategy(**params),
        "macd_trend": lambda params: MacdTrendStrategy(**params),
        "bollinger_reversion": lambda params: BollingerReversionStrategy(**params),
    }

    engine = PaperTradingEngine(dhan_client=dhan_client, session=session)

    configs = session.query(BotConfig).filter_by(enabled=True).all()
    for config in configs:
        strategy_row = session.get(Strategy, config.strategy_id)
        instrument = session.get(Instrument, config.instrument_id)
        if strategy_row is None or instrument is None:
            continue

        builder = strategy_builders.get(strategy_row.name)
        if builder is None:
            logger.warning("Unknown strategy %s, skipping", strategy_row.name)
            continue
        strategy = builder(strategy_row.params or {})

        open_position = (
            session.query(PaperPosition)
            .filter_by(
                strategy_id=config.strategy_id,
                instrument_id=config.instrument_id,
                status="open",
            )
            .one_or_none()
        )
        current_qty = float(open_position.quantity) if open_position else 0.0
        avg_entry_price = float(open_position.avg_entry_price) if open_position else None
        position_id = open_position.id if open_position else None
        daily_pnl = _cumulative_daily_pnl(session, config.strategy_id, config.instrument_id, now)

        engine.process_tick(
            config=config,
            instrument=instrument,
            strategy=strategy,
            current_position_qty=current_qty,
            avg_entry_price=avg_entry_price,
            paper_position_id=position_id,
            cumulative_daily_pnl=daily_pnl,
        )
    session.commit()


def start(poll_interval_seconds: Optional[int] = None) -> None:
    """Start the APScheduler polling loop. Blocks forever; run as the main process."""
    from apscheduler.schedulers.blocking import BlockingScheduler

    from growmore_bot.broker.dhan_client import DhanClient
    from growmore_bot.config import Settings
    from growmore_bot.persistence.db import session_scope

    settings = Settings()
    interval = poll_interval_seconds or settings.default_polling_interval_seconds
    dhan_client = DhanClient(
        client_id=settings.dhan_client_id, access_token=settings.dhan_access_token
    )

    def _job() -> None:
        dhan_client.refresh_access_token_if_needed()
        with session_scope() as session:
            tick(run_all_configs=lambda: run_all_enabled_configs(session, dhan_client))

    scheduler = BlockingScheduler(timezone=MCX_TIMEZONE)
    scheduler.add_job(_job, "interval", seconds=interval, next_run_time=datetime.now(MCX_TIMEZONE))
    logger.info("Starting scheduler, polling every %s seconds", interval)
    scheduler.start()


__all__ = ["tick", "run_all_enabled_configs", "start"]
