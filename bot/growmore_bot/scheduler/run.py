"""Market-hours-aware scheduler for the paper trading engine.

Not a tick-driven daemon / not HFT: APScheduler polls on
`Settings().default_polling_interval_seconds` (default 5 minutes) and only
does anything when `is_market_open()` says MCX is trading.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

from growmore_bot.broker.instrument_master import fetch_instrument_master_csv
from growmore_bot.scheduler.contract_rollover import is_past_close_out_cutoff, roll_to_next_contract
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


def _update_bot_status(session: Any, live_trading_enabled: bool, dhan_client: Any, now: datetime) -> None:
    """Upsert the singleton bot_status row -- called every tick regardless
    of market hours, so "last tick N minutes ago" reflects real process
    health, not just "did we trade." A failed fund-limits fetch (network
    hiccup, etc.) is logged and swallowed, keeping the last known balance --
    it must never crash the tick or hide that the process is alive.
    """
    from growmore_bot.persistence.models import BotStatus

    status = session.query(BotStatus).first()
    available_balance = status.available_balance if status is not None else None
    utilized_margin = status.utilized_margin if status is not None else None
    try:
        funds = dhan_client.get_fund_limits()
        available_balance = funds.available_balance
        utilized_margin = funds.utilized_amount
    except Exception:
        logger.exception("Failed to fetch fund limits -- bot_status keeps its last known balance")

    if status is None:
        session.add(
            BotStatus(
                id=uuid.uuid4(),
                live_trading_enabled=live_trading_enabled,
                last_tick_at=now,
                available_balance=available_balance,
                utilized_margin=utilized_margin,
            )
        )
    else:
        status.live_trading_enabled = live_trading_enabled
        status.last_tick_at = now
        status.available_balance = available_balance
        status.utilized_margin = utilized_margin
        session.add(status)


def _cumulative_daily_pnl(
    session: Any,
    strategy_id: Any,
    instrument_id: Any,
    now: datetime,
    order_model: Any = None,
    position_model: Any = None,
    position_fk_attr: str = "paper_position_id",
) -> float:
    """Sum today's (MCX-timezone calendar day) realized P&L for this
    strategy/instrument pair, from an order model's `.pnl` (set only on sell
    fills -- see PaperTradingEngine._handle_sell / LiveTradingEngine's
    equivalents). A position model's `realized_pnl` is cumulative-ever, not
    per-day, so it can't answer "has today breached the daily loss limit" on
    its own.

    Defaults to PaperOrder/PaperPosition; pass `order_model=LiveOrder,
    position_model=LivePosition, position_fk_attr="live_position_id"` for a
    live-mode bot_config.
    """
    from growmore_bot.persistence.models import PaperOrder, PaperPosition

    order_model = order_model or PaperOrder
    position_model = position_model or PaperPosition

    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    fk_column = getattr(order_model, position_fk_attr)
    rows = (
        session.query(order_model.pnl)
        .join(position_model, fk_column == position_model.id)
        .filter(
            position_model.strategy_id == strategy_id,
            position_model.instrument_id == instrument_id,
            order_model.filled_at >= day_start,
            order_model.pnl.isnot(None),
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


def run_all_enabled_configs(
    session: Any,
    dhan_client: Any,
    now: Optional[datetime] = None,
    order_client: Optional[Any] = None,
    live_trading_enabled: bool = False,
) -> None:
    """Fetch every enabled bot_config row and run one trading tick each.

    Strategy instances are built fresh each tick from `strategies.name` +
    `strategies.params` -- this keeps the scheduler stateless between ticks
    (position state is read from the DB, not held in memory).

    Each bot_config's `mode` ("paper", the default, or "live") picks
    PaperTradingEngine/paper_positions or LiveTradingEngine/live_positions --
    see CLAUDE.md non-negotiables. A `mode="live"` config is skipped
    ENTIRELY (not silently downgraded to paper trading) whenever
    `live_trading_enabled` is False -- both gates must be open for a real
    order to ever be considered. `order_client` (a DhanOrderClient) is only
    required when at least one enabled config is actually live; `start()`
    only constructs one when `Settings().live_trading_enabled` is True.
    """
    from growmore_bot.live.engine import LiveTradingEngine
    from growmore_bot.paper.engine import PaperTradingEngine
    from growmore_bot.persistence.models import (
        BotConfig,
        BotSignalState,
        Instrument,
        LivePosition,
        PaperPosition,
        Strategy,
    )
    from growmore_bot.strategies.always_flip import AlwaysFlipStrategy
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
        # Demo-only, not a real trading strategy -- see always_flip.py.
        "always_flip": lambda params: AlwaysFlipStrategy(**params),
    }

    paper_engine = PaperTradingEngine(dhan_client=dhan_client, session=session)
    live_engine = (
        LiveTradingEngine(dhan_client=dhan_client, order_client=order_client, session=session)
        if order_client is not None
        else None
    )
    if live_engine is not None:
        # Once per tick, not per-config -- reconciles every still-pending
        # LiveOrder regardless of which config placed it.
        live_engine.reconcile_pending_orders()

    configs = session.query(BotConfig).filter_by(enabled=True).all()
    for config in configs:
        strategy_row = session.get(Strategy, config.strategy_id)
        instrument = session.get(Instrument, config.instrument_id)
        if strategy_row is None or instrument is None:
            continue

        label = f"{strategy_row.name} {strategy_row.version} / {instrument.symbol}"
        is_live = getattr(config, "mode", "paper") == "live"

        if is_live and not live_trading_enabled:
            # Both gates (bot_config.mode AND the global kill switch) must be
            # open -- never fall back to paper trading for a config marked
            # live, that would silently hide a misconfiguration.
            logger.warning(
                "%s is mode=live but live_trading_enabled is globally False -- skipping entirely "
                "this tick (not falling back to paper trading)",
                label,
            )
            continue

        position_model = LivePosition if is_live else PaperPosition
        open_position = (
            session.query(position_model)
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

        if is_past_close_out_cutoff(instrument.symbol, instrument.contract_expiry, now.date()):
            # Dhan does not permit physical delivery for retail clients --
            # see growmore_bot.scheduler.contract_rollover. Force-close
            # whatever's open and never evaluate the strategy at all this
            # tick, so no fresh position can be opened either. Resumes
            # automatically once this instrument's security_id/
            # contract_expiry are rolled to the next contract month.
            if is_live:
                live_engine.force_close_for_expiry(
                    config=config,
                    instrument=instrument,
                    current_position_qty=current_qty,
                    avg_entry_price=avg_entry_price,
                    live_position_id=position_id,
                    label=label,
                )
            else:
                paper_engine.force_close_for_expiry(
                    config=config,
                    instrument=instrument,
                    current_position_qty=current_qty,
                    avg_entry_price=avg_entry_price,
                    paper_position_id=position_id,
                    label=label,
                )
            try:
                csv_text = fetch_instrument_master_csv()
                roll_to_next_contract(session, dhan_client, instrument, csv_text)
            except Exception:
                # A failed automatic rollover attempt (network hiccup, Dhan's
                # instrument master temporarily unreachable, etc.) just means
                # this tick falls back to the existing manual process -- the
                # close-out guard above already made sure nothing unsafe
                # happens in the meantime. Retried again next tick.
                logger.exception(
                    "Automatic contract rollover attempt failed for %s -- will retry next tick "
                    "(manual rollover remains available in the meantime)",
                    label,
                )
            continue

        builder = strategy_builders.get(strategy_row.name)
        if builder is None:
            logger.warning("Unknown strategy %s, skipping", strategy_row.name)
            continue
        strategy = builder(strategy_row.params or {})
        _warm_up_strategy(strategy, dhan_client, instrument, now)

        # Restore the crossing/threshold-recovery reference from the last
        # LIVE tick (see Strategy.get_state_snapshot's docstring) -- without
        # this, warm-up alone always leaves that reference at whatever it
        # was at yesterday's close, so a signal meant to fire once would
        # re-fire every tick for the rest of the day. Found live 2026-09-04.
        signal_state = (
            session.query(BotSignalState).filter_by(bot_config_id=config.id).one_or_none()
        )
        if signal_state is not None and signal_state.crossing_state:
            strategy.load_state_snapshot(signal_state.crossing_state)

        if is_live:
            from growmore_bot.persistence.models import LiveOrder

            daily_pnl = _cumulative_daily_pnl(
                session,
                config.strategy_id,
                config.instrument_id,
                now,
                order_model=LiveOrder,
                position_model=LivePosition,
                position_fk_attr="live_position_id",
            )
        else:
            daily_pnl = _cumulative_daily_pnl(session, config.strategy_id, config.instrument_id, now)

        if is_live:
            live_engine.process_tick(
                config=config,
                instrument=instrument,
                strategy=strategy,
                current_position_qty=current_qty,
                avg_entry_price=avg_entry_price,
                live_position_id=position_id,
                cumulative_daily_pnl=daily_pnl,
                strategy_label=f"{strategy_row.name} {strategy_row.version}",
            )
        else:
            paper_engine.process_tick(
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

    Also forces a fresh session once per calendar day (IST), independent of
    how much validity the current token has left -- SEBI's retail algo API
    guidance expects an automatic session reset before each trading day, not
    just a reactive refresh once a token is about to expire. Tracked via
    `last_forced_reset_date`, held in this closure -- resets to None (so the
    very next tick forces a reset) whenever the process restarts, which is
    itself a reasonable proxy for "start of a fresh session" too.
    """
    from apscheduler.schedulers.blocking import BlockingScheduler

    from growmore_bot.broker.dhan_client import DhanClient
    from growmore_bot.broker.token_refresh import (
        DhanTokenRefreshError,
        is_new_trading_day,
        refresh_if_needed,
    )
    from growmore_bot.config import _REPO_ROOT_ENV_LOCAL, Settings
    from growmore_bot.persistence.db import session_scope

    initial_settings = Settings()
    interval = poll_interval_seconds or initial_settings.default_polling_interval_seconds
    last_forced_reset_date: Optional[date] = None

    def _job() -> None:
        nonlocal last_forced_reset_date
        settings = Settings()  # re-read each tick to notice an externally-refreshed token

        if settings.dhan_pin and settings.dhan_totp_secret:
            today = datetime.now(MCX_TIMEZONE).date()
            force = is_new_trading_day(last_forced_reset_date, today)
            try:
                if refresh_if_needed(
                    current_token=settings.dhan_access_token,
                    client_id=settings.dhan_client_id,
                    pin=settings.dhan_pin,
                    totp_secret=settings.dhan_totp_secret,
                    env_file=_REPO_ROOT_ENV_LOCAL,
                    force=force,
                ):
                    settings = Settings()  # re-read again to pick up the just-written token
                    if force:
                        last_forced_reset_date = today
            except DhanTokenRefreshError:
                logger.exception("Automatic Dhan token refresh failed")

        dhan_client = DhanClient(
            client_id=settings.dhan_client_id, access_token=settings.dhan_access_token
        )
        # Final safety check -- raises clearly if the token is still expired
        # (e.g. PIN/TOTP not configured, or the refresh above just failed).
        dhan_client.refresh_access_token_if_needed()

        with session_scope() as session:
            # Always runs, regardless of market hours -- staleness/health
            # must reflect the process being alive, not just "did we trade."
            # Committed immediately (not just at the end of this session's
            # transaction) so it stays accurate even if something later in
            # this same tick fails and rolls the rest back.
            _update_bot_status(
                session,
                live_trading_enabled=settings.live_trading_enabled,
                dhan_client=dhan_client,
                now=datetime.now(timezone.utc),
            )
            session.commit()

            order_client = None
            if settings.live_trading_enabled:
                # Only ever constructed when the global kill switch is on --
                # see CLAUDE.md non-negotiables. Still requires a config's
                # own mode="live" (checked in run_all_enabled_configs) before
                # anything actually gets ordered.
                from growmore_bot.broker.dhan_order_client import DhanOrderClient

                order_client = DhanOrderClient(
                    client_id=settings.dhan_client_id,
                    access_token=settings.dhan_access_token,
                    live_trading_enabled=settings.live_trading_enabled,
                    session=session,
                )
            tick(
                run_all_configs=lambda: run_all_enabled_configs(
                    session,
                    dhan_client,
                    order_client=order_client,
                    live_trading_enabled=settings.live_trading_enabled,
                )
            )

    scheduler = BlockingScheduler(timezone=MCX_TIMEZONE)
    scheduler.add_job(_job, "interval", seconds=interval, next_run_time=datetime.now(MCX_TIMEZONE))
    logger.info("Starting scheduler, polling every %s seconds", interval)
    scheduler.start()


__all__ = ["tick", "run_all_enabled_configs", "start"]
