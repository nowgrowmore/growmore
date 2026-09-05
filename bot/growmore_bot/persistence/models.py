"""SQLAlchemy 2.0 ORM models for the shared Neon Postgres schema.

Mirrors docs/db-schema.md exactly: table names, columns, and FK relationships.
Conventions (per docs/db-schema.md "Notes"):
  - UUID primary keys.
  - `numeric` for all money/price columns -- never float.
  - `timestamptz` for all timestamps.

This module owns the schema; the dashboard (dashboard/) only reads it plus
writes enable/disable toggles into bot_config. Migrations live in
growmore_bot/persistence/migrations/ (Alembic).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Generic types that render as their efficient Postgres-native equivalent in
# production but still work against SQLite in unit tests (no real DB needed).
UUID = Uuid(as_uuid=True)
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID, primary_key=True, default=uuid.uuid4)


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    exchange_segment: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. MCX_COMM
    security_id: Mapped[str] = mapped_column(Text, nullable=False)  # Dhan instrument id
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Real contract trading unit (e.g. Gold Mini=100, Copper=2500 -- looked up from MCX's official
    # contract specs, never guessed). Without this, BacktestEngine treated every instrument as "1
    # raw unit of the price series," which made Sharpe/Max Drawdown incomparable across commodities
    # at very different price levels (confirmed 2026-09-03: base metals showed implausibly tiny
    # drawdowns purely from this scaling bug). Defaults to 1 for backward compatibility.
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    # Current front-month contract's last trading day (from
    # CommodityPlaceholder.contract_expiry in config.py -- looked up from real
    # MCX contract specs, never guessed). Nullable: purely informational for
    # display (dashboard trade log/positions), not read by any trading logic.
    # Will need updating at each contract roll, same as security_id/lot_size.
    contract_expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Minimum price increment, in the instrument's own quote units (e.g.
    #: Rs 0.05/kg for Copper, Rs 1 per 10g for Gold Mini). Slippage is a tick
    #: effect, not a basis-point one -- one Copper tick is Rs 125 a lot, so a
    #: flat bps assumption would rank the instruments backwards. Nullable:
    #: rows predating growmore_bot.costs simply have no tick recorded.
    tick_size: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    backtest_runs: Mapped[list["BacktestRun"]] = relationship(back_populates="instrument")
    paper_positions: Mapped[list["PaperPosition"]] = relationship(back_populates="instrument")
    bot_configs: Mapped[list["BotConfig"]] = relationship(back_populates="instrument")


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)

    backtest_runs: Mapped[list["BacktestRun"]] = relationship(back_populates="strategy")
    paper_positions: Mapped[list["PaperPosition"]] = relationship(back_populates="strategy")
    bot_configs: Mapped[list["BotConfig"]] = relationship(back_populates="strategy")


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("strategies.id"), nullable=False
    )
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("instruments.id"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sharpe_ratio: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    win_rate_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    profit_factor: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    cagr_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    #: The capital this run was actually measured against. Nullable only for
    #: rows written before capital became per-instrument, which were all
    #: measured against one flat figure regardless of lot notional -- so
    #: their CAGR ranks contract size as much as edge and is not comparable
    #: with a row that has this set.
    initial_capital: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    total_transaction_cost: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    #: CAGR before costs. `cagr_pct` is net; keeping both makes the cost drag
    #: auditable rather than something you have to take on trust.
    gross_cagr_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    cost_model: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    #: Deflated Sharpe Ratio -- the probability this run's Sharpe reflects
    #: real edge rather than being the luckiest of everything tried in the
    #: sweep it was part of (see bot/growmore_bot/backtest/deflated_sharpe.py
    #: and research/validation/deflate_sweep.py --persist). Null until that
    #: batch computation has been run against the current sweep; it is a
    #: sweep-relative statistic (depends on every other run's correlation
    #: structure), not something a single run computes for itself, so it
    #: goes stale if the sweep changes without a fresh --persist run.
    dsr: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    strategy: Mapped["Strategy"] = relationship(back_populates="backtest_runs")
    instrument: Mapped["Instrument"] = relationship(back_populates="backtest_runs")
    trades: Mapped[list["BacktestTrade"]] = relationship(
        back_populates="backtest_run", cascade="all, delete-orphan"
    )
    equity_curve_points: Mapped[list["EquityCurvePoint"]] = relationship(
        back_populates="backtest_run", cascade="all, delete-orphan"
    )


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"

    id: Mapped[uuid.UUID] = _uuid_pk()
    backtest_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("backtest_runs.id"), nullable=False
    )
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    side: Mapped[str] = mapped_column(Text, nullable=False)  # buy|sell
    entry_price: Mapped[float] = mapped_column(Numeric, nullable=False)
    exit_price: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    backtest_run: Mapped["BacktestRun"] = relationship(back_populates="trades")


class EquityCurvePoint(Base):
    __tablename__ = "equity_curve_points"

    id: Mapped[uuid.UUID] = _uuid_pk()
    backtest_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("backtest_runs.id"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    equity: Mapped[float] = mapped_column(Numeric, nullable=False)

    backtest_run: Mapped["BacktestRun"] = relationship(back_populates="equity_curve_points")


class PaperPosition(Base):
    __tablename__ = "paper_positions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("strategies.id"), nullable=False
    )
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("instruments.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")  # open|closed
    quantity: Mapped[float] = mapped_column(Numeric, nullable=False)
    avg_entry_price: Mapped[float] = mapped_column(Numeric, nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Numeric, nullable=False, default=0)
    unrealized_pnl: Mapped[float] = mapped_column(Numeric, nullable=False, default=0)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Per-trade risk state for RiskManagedStrategy (stop_price, high_water,
    # entry_atr, bars_held, direction) -- round-tripped through
    # position_state["risk"] every tick so the wrapper's computed stop
    # actually persists instead of resetting each tick. Empty {} for a
    # position opened by a non-risk-managed strategy.
    risk_state: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)

    strategy: Mapped["Strategy"] = relationship(back_populates="paper_positions")
    instrument: Mapped["Instrument"] = relationship(back_populates="paper_positions")
    orders: Mapped[list["PaperOrder"]] = relationship(
        back_populates="paper_position", cascade="all, delete-orphan"
    )


class PaperOrder(Base):
    __tablename__ = "paper_orders"

    id: Mapped[uuid.UUID] = _uuid_pk()
    paper_position_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("paper_positions.id"), nullable=False
    )
    side: Mapped[str] = mapped_column(Text, nullable=False)  # buy|sell
    quantity: Mapped[float] = mapped_column(Numeric, nullable=False)
    simulated_fill_price: Mapped[float] = mapped_column(Numeric, nullable=False)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Realized P&L this specific sell fill locked in (lot-size-scaled), NULL
    # for buy fills. Needed to compute a strategy/instrument's cumulative
    # *daily* P&L for the daily_loss_limit risk guard -- PaperPosition only
    # tracks cumulative-ever realized_pnl, not a per-day breakdown.
    pnl: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    # Why this sell fired -- "strategy_signal" (the ordinary case), "expiry"
    # or "end_of_day" (a force-close, same string _force_close already used
    # for audit-log labeling), or "daily_loss_limit" (the risk guard tripped
    # and auto-closed). NULL for buy fills (not applicable) and for any
    # historical row predating this column.
    close_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: `pnl` above is NET of costs. These three record what was deducted, so
    #: gross-vs-net is auditable. Nullable for rows predating the cost model.
    gross_pnl: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    transaction_cost: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    slippage_cost: Mapped[float | None] = mapped_column(Numeric, nullable=True)


    paper_position: Mapped["PaperPosition"] = relationship(back_populates="orders")


class BotConfig(Base):
    __tablename__ = "bot_config"

    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("strategies.id"), nullable=False
    )
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("instruments.id"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # `virtual_capital` was dropped in migration 0020: nothing in the trading
    # path ever read it, and displaying Rs 2.5 lakh against a Rs 15.2 lakh Gold
    # Mini lot implied leverage the bot was not taking. Position size is
    # `max_position_size`, in lots, which is the unit the exchange deals in.
    max_position_size: Mapped[float] = mapped_column(Numeric, nullable=False)
    daily_loss_limit: Mapped[float] = mapped_column(Numeric, nullable=False)
    # RETIRED 2026-09-05 (migration 0019) -- server_default is now "false"
    # and every existing row was set false, by the account owner's decision.
    # The mechanism's failure mode was the problem: tripping it sets
    # `enabled = False` permanently rather than pausing, and it compares
    # REALISED P&L to a flat rupee figure that happened to be ~1% of one Gold
    # Mini lot's notional -- so one ordinary 1% down day would switch a
    # working config off for good. Loss control lives in the strategies' ATR
    # stops instead, where it is visible and backtested.
    # When false, cumulative daily P&L is never checked against
    # daily_loss_limit at all: no auto-close, no auto-disable, purely the
    # strategy's own BUY/SELL signals govern entries and exits. The
    # end-of-day-flatten and contract-expiry force-closes are position-
    # lifecycle safety nets, not P&L risk management, and stay active
    # regardless of this flag.
    daily_loss_limit_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # "paper" (default) or "live" -- a REAL order is only ever placed when
    # this is "live" AND Settings().live_trading_enabled is also True (see
    # CLAUDE.md non-negotiables and growmore_bot/scheduler/run.py). Two
    # independent gates, deliberately: flipping one alone never enables real
    # trading.
    mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="paper")
    # Set by LiveTradingEngine._trip_daily_loss_guard when a real auto-close
    # order fails -- the config stays disabled (no fresh trades) but
    # retry_pending_auto_close keeps retrying the close itself on later
    # ticks until it succeeds, using these to back off geometrically instead
    # of hammering Dhan every 5 minutes. See docs/technical-debt.md.
    pending_auto_close: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_close_retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    auto_close_next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    strategy: Mapped["Strategy"] = relationship(back_populates="bot_configs")
    instrument: Mapped["Instrument"] = relationship(back_populates="bot_configs")


class BotSignalState(Base):
    """The most recent tick's signal + computed indicator values for one
    bot_config -- one row per config, upserted every tick (not a history
    log; only "what did the strategy see just now" matters here). Lets the
    dashboard show "this strategy is currently HOLD/BUY/SELL, here's how
    close it is" without needing to grep bot.log. Written by
    PaperTradingEngine/LiveTradingEngine right after computing a signal,
    regardless of what the signal was.
    """

    __tablename__ = "bot_signal_state"

    id: Mapped[uuid.UUID] = _uuid_pk()
    bot_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("bot_config.id"), nullable=False, unique=True
    )
    last_signal: Mapped[str] = mapped_column(Text, nullable=False)  # HOLD|BUY|SELL
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ltp: Mapped[float] = mapped_column(Numeric, nullable=False)
    # Previous trading day's close (Dhan's quote "close" field -- during live
    # hours this can only be yesterday's close, since today's hasn't happened
    # yet, confirmed against a real quote 2026-09-04). Lets the dashboard show
    # today's % change without needing its own live Dhan connection.
    prev_close: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    # Today's cumulative realized P&L for this (strategy, instrument) pair --
    # the same value the daily_loss_limit risk guard checks against every
    # tick (see scheduler.run._cumulative_daily_pnl). Lets the dashboard show
    # progress toward the limit before it trips, not just after.
    daily_pnl: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    # Strategy.debug_state()'s raw dict, e.g. {"macd": -1113.34, "signal": 363.55}.
    indicators: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    # Strategy.get_state_snapshot()'s raw dict, e.g. {"prev_macd_above_signal":
    # True} -- restored via Strategy.load_state_snapshot() after the next
    # tick's historical warm-up but before evaluating the live quote, so a
    # crossing/threshold-recovery signal compares against the last LIVE
    # tick's state, not always yesterday's close (see Strategy.
    # get_state_snapshot's docstring for why this matters -- found live
    # 2026-09-04). Distinct from `indicators`, which is display-only.
    crossing_state: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    # When a max_position_size rejection was last actually WRITTEN to
    # audit_log for this config -- lets _handle_buy throttle repeat entries
    # to once per 30 minutes instead of once per tick when an indicator
    # chops back and forth across its crossing threshold while a position is
    # already open (a real, repeated signal, but low marginal audit-log
    # value once already recorded recently). bot.log still gets a warning
    # every single time regardless -- only the audit_log write is throttled.
    last_max_position_rejection_logged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    bot_config: Mapped["BotConfig"] = relationship()


class SignalHistory(Base):
    """One row per tick per bot_config, regardless of what the signal was --
    unlike `BotSignalState` (upsert-only, "what did the strategy see just
    now"), this is a genuine append-only log, specifically so the dashboard
    can show a short "HOLD HOLD HOLD BUY HOLD" recent-signal strip. Written
    right after BotSignalState's own upsert in
    PaperTradingEngine/LiveTradingEngine._record_signal_state.
    """

    __tablename__ = "signal_history"

    id: Mapped[uuid.UUID] = _uuid_pk()
    bot_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("bot_config.id"), nullable=False
    )
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)  # HOLD|BUY|SELL
    ltp: Mapped[float] = mapped_column(Numeric, nullable=False)


class LivePosition(Base):
    """Mirrors PaperPosition exactly, but for REAL orders placed via
    growmore_bot.broker.dhan_order_client -- kept as an entirely separate
    table (not a shared "mode" column on paper_positions) so real and
    simulated trading data can never be confused with each other, in the
    database or on the dashboard.
    """

    __tablename__ = "live_positions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("strategies.id"), nullable=False
    )
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("instruments.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")  # open|closed
    quantity: Mapped[float] = mapped_column(Numeric, nullable=False)
    avg_entry_price: Mapped[float] = mapped_column(Numeric, nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Numeric, nullable=False, default=0)
    unrealized_pnl: Mapped[float] = mapped_column(Numeric, nullable=False, default=0)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # See PaperPosition.risk_state -- same meaning.
    risk_state: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    # The resting real STOP_LOSS_MARKET order protecting this position (see
    # DhanOrderClient.place_stop_loss_market_order), if a risk-managed
    # strategy placed one. NULL for a non-risk-managed config, or before the
    # entry's stop order has been placed yet.
    stop_order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    stop_order_trigger_price: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    strategy: Mapped["Strategy"] = relationship()
    instrument: Mapped["Instrument"] = relationship()
    orders: Mapped[list["LiveOrder"]] = relationship(
        back_populates="live_position", cascade="all, delete-orphan"
    )


class LiveOrder(Base):
    """Mirrors PaperOrder, plus `broker_order_id` -- Dhan's own order ID,
    needed to look the order up again on Dhan's side for reconciliation.
    """

    __tablename__ = "live_orders"

    id: Mapped[uuid.UUID] = _uuid_pk()
    live_position_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("live_positions.id"), nullable=False
    )
    side: Mapped[str] = mapped_column(Text, nullable=False)  # buy|sell
    quantity: Mapped[float] = mapped_column(Numeric, nullable=False)
    broker_order_id: Mapped[str] = mapped_column(Text, nullable=False)
    order_status: Mapped[str] = mapped_column(Text, nullable=False)  # Dhan's orderStatus
    fill_price: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pnl: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    # See PaperOrder.close_reason -- same values, same meaning.
    close_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: `pnl` above is NET of costs. These three record what was deducted, so
    #: gross-vs-net is auditable. Nullable for rows predating the cost model.
    gross_pnl: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    transaction_cost: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    slippage_cost: Mapped[float | None] = mapped_column(Numeric, nullable=True)


    live_position: Mapped["LivePosition"] = relationship(back_populates="orders")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = _uuid_pk()
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)


class BotStatus(Base):
    """Singleton row (always exactly one) -- the scheduler upserts this
    every tick regardless of market hours, so the dashboard can show
    whether the process is actually alive ("last tick N minutes ago") and
    whether the real-money kill switch is armed, without SSHing into the
    host. Also carries the real account balance (read-only, GET
    /fundlimit) so the dashboard doesn't need its own Dhan connection.
    """

    __tablename__ = "bot_status"

    id: Mapped[uuid.UUID] = _uuid_pk()
    live_trading_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_tick_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_balance: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    utilized_margin: Mapped[float | None] = mapped_column(Numeric, nullable=True)


class PortfolioBacktestRun(Base):
    """A single run of the cross-sectional momentum(+quality) small-cap/
    mid-cap research backtest (bot/research/smallcap_momentum/) -- distinct
    from `BacktestRun`, which is always ONE strategy on ONE instrument.
    Deliberately NOT linked to `strategies`/`instruments` (a portfolio run
    holds a rotating basket of many instruments, not one) -- `universe`/
    `variant` identify the run instead. See
    docs/smallcap-momentum-backtest-results.md for real results and every
    caveat (this is real-data research on an asset class the bot doesn't
    trade, not a strategy ever eligible to go live).
    """

    __tablename__ = "portfolio_backtest_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    universe: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. "smallcap250"
    variant: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. "momentum_quality_trend"
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    top_n: Mapped[int] = mapped_column(Integer, nullable=False)
    initial_capital: Mapped[float] = mapped_column(Numeric, nullable=False)
    final_equity: Mapped[float] = mapped_column(Numeric, nullable=False)
    rebalance_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sharpe_ratio: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    win_rate_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    cagr_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    quality_coverage_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    equity_curve_points: Mapped[list["PortfolioEquityCurvePoint"]] = relationship(
        back_populates="portfolio_backtest_run", cascade="all, delete-orphan"
    )
    holdings: Mapped[list["PortfolioRebalanceHolding"]] = relationship(
        back_populates="portfolio_backtest_run", cascade="all, delete-orphan"
    )


class PortfolioEquityCurvePoint(Base):
    __tablename__ = "portfolio_equity_curve_points"

    id: Mapped[uuid.UUID] = _uuid_pk()
    portfolio_backtest_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("portfolio_backtest_runs.id"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    equity: Mapped[float] = mapped_column(Numeric, nullable=False)

    portfolio_backtest_run: Mapped["PortfolioBacktestRun"] = relationship(
        back_populates="equity_curve_points"
    )


class PortfolioRebalanceHolding(Base):
    """One row per (rebalance date, held symbol) -- what the strategy
    actually held, for drill-down/spot-checking on the dashboard.
    """

    __tablename__ = "portfolio_rebalance_holdings"

    id: Mapped[uuid.UUID] = _uuid_pk()
    portfolio_backtest_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("portfolio_backtest_runs.id"), nullable=False
    )
    rebalance_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[float] = mapped_column(Numeric, nullable=False)
    composite_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    portfolio_backtest_run: Mapped["PortfolioBacktestRun"] = relationship(back_populates="holdings")


__all__ = [
    "Base",
    "Instrument",
    "Strategy",
    "BacktestRun",
    "BacktestTrade",
    "EquityCurvePoint",
    "PaperPosition",
    "PaperOrder",
    "LivePosition",
    "LiveOrder",
    "BotConfig",
    "BotSignalState",
    "AuditLog",
    "BotStatus",
    "PortfolioBacktestRun",
    "PortfolioEquityCurvePoint",
    "PortfolioRebalanceHolding",
]
