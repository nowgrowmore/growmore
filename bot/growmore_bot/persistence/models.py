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
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Numeric, Text, Uuid, func
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
    virtual_capital: Mapped[float] = mapped_column(Numeric, nullable=False)
    max_position_size: Mapped[float] = mapped_column(Numeric, nullable=False)
    daily_loss_limit: Mapped[float] = mapped_column(Numeric, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    strategy: Mapped["Strategy"] = relationship(back_populates="bot_configs")
    instrument: Mapped["Instrument"] = relationship(back_populates="bot_configs")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = _uuid_pk()
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)


__all__ = [
    "Base",
    "Instrument",
    "Strategy",
    "BacktestRun",
    "BacktestTrade",
    "EquityCurvePoint",
    "PaperPosition",
    "PaperOrder",
    "BotConfig",
    "AuditLog",
]
