"""Market-hours-aware scheduler for the paper trading engine.

Not a tick-driven daemon / not HFT: APScheduler polls on
`Settings().default_polling_interval_seconds` (default 5 minutes) and only
does anything when `is_market_open()` says MCX is trading.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from growmore_bot.scheduler.market_hours import MCX_TIMEZONE, is_market_open

logger = logging.getLogger(__name__)

# Longest lookback any strategy variant in the parameter grid needs (Donchian
# period=55) plus real margin for weekends/MCX holidays.
DEFAULT_WARMUP_LOOKBACK_DAYS = 150


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


def _warm_up_strategy(strategy: Any, dhan_client: Any, instrument: Any, now: datetime) -> None:
    """Rebuild a freshly-constructed strategy's indicator state from real
    historical daily bars, up to (not including) today.

    A fresh strategy instance is built every tick (see
    run_all_enabled_configs) so the scheduler itself stays stateless -- but
    without this, an indicator needing N bars of history (e.g. MACD needs
    13+) would be fed exactly one live price per tick and thrown away,
    never accumulating enough history to produce a real signal no matter
    how long the bot ran. Found running this for real the first time --
    see docs/technical-debt.md. Stops at yesterday's close since the live
    quote fed right after this call represents today's still-forming bar,
    matching what the backtest engine's next-bar-open fill discipline
    assumes: one bar per day, not one bar per 5-minute poll.

    Failures here are swallowed (logged, not raised) -- a warm-up hiccup on
    one tick shouldn't crash the whole scheduler; the next tick tries again
    with a fresh fetch.
    """
    to_date = (now.date() - timedelta(days=1)).isoformat()
    from_date = (now.date() - timedelta(days=DEFAULT_WARMUP_LOOKBACK_DAYS)).isoformat()
    try:
        bars = dhan_client.get_historical_ohlc(
            instrument, from_date=from_date, to_date=to_date, interval="day"
        )
    except Exception:
        logger.exception(
            "Failed to fetch warm-up history for instrument_id=%s -- strategy will run unwarmed "
            "this tick",
            getattr(instrument, "id", instrument),
        )
        return
    for bar in bars:
        strategy.on_bar(bar, None)


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
        _warm_up_strategy(strategy, dhan_client, instrument, now)

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
            strategy_label=f"{strategy_row.name} {strategy_row.version}",
        )
    session.commit()


def start(poll_interval_seconds: Optional[int] = None) -> None:
    """Start the APScheduler polling loop. Blocks forever; run as the main process.

    Settings are re-read fresh from disk every tick (cheap -- just parses the
    env file, no network call) rather than captured once at startup, so a
    long-running process picks up a token refreshed by
    growmore_bot.broker.token_refresh -- either run separately (e.g. a daily
    cron before market open) or automatically here each tick if DHAN_PIN and
    DHAN_TOTP_SECRET are configured. Without those two set, this falls back
    to the old behaviour: refresh_access_token_if_needed() raises a clear
    error once the token actually expires, same as before.
    """
    from apscheduler.schedulers.blocking import BlockingScheduler

    from growmore_bot.broker.dhan_client import DhanClient
    from growmore_bot.broker.token_refresh import DhanTokenRefreshError, refresh_if_needed
    from growmore_bot.config import _REPO_ROOT_ENV_LOCAL, Settings
    from growmore_bot.persistence.db import session_scope

    initial_settings = Settings()
    interval = poll_interval_seconds or initial_settings.default_polling_interval_seconds

    def _job() -> None:
        settings = Settings()  # re-read each tick to notice an externally-refreshed token

        if settings.dhan_pin and settings.dhan_totp_secret:
            try:
                if refresh_if_needed(
                    current_token=settings.dhan_access_token,
                    client_id=settings.dhan_client_id,
                    pin=settings.dhan_pin,
                    totp_secret=settings.dhan_totp_secret,
                    env_file=_REPO_ROOT_ENV_LOCAL,
                ):
                    settings = Settings()  # re-read again to pick up the just-written token
            except DhanTokenRefreshError:
                logger.exception("Automatic Dhan token refresh failed")

        dhan_client = DhanClient(
            client_id=settings.dhan_client_id, access_token=settings.dhan_access_token
        )
        # Final safety check -- raises clearly if the token is still expired
        # (e.g. PIN/TOTP not configured, or the refresh above just failed).
        dhan_client.refresh_access_token_if_needed()

        with session_scope() as session:
            tick(run_all_configs=lambda: run_all_enabled_configs(session, dhan_client))

    scheduler = BlockingScheduler(timezone=MCX_TIMEZONE)
    scheduler.add_job(_job, "interval", seconds=interval, next_run_time=datetime.now(MCX_TIMEZONE))
    logger.info("Starting scheduler, polling every %s seconds", interval)
    scheduler.start()


__all__ = ["tick", "run_all_enabled_configs", "start"]
