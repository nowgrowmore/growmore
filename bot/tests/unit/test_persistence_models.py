"""Tests for the SQLAlchemy models in growmore_bot.persistence.models.

These are schema-shape tests (no DB required) -- they check that the ORM
metadata matches docs/db-schema.md: table names, PK types, FK relationships,
and that money/price columns use NUMERIC (never Float).
"""
from __future__ import annotations

import uuid

from sqlalchemy import Numeric

from growmore_bot.persistence.models import Base, Instrument, Strategy

EXPECTED_TABLES = {
    "instruments",
    "strategies",
    "backtest_runs",
    "backtest_trades",
    "equity_curve_points",
    "paper_positions",
    "paper_orders",
    "live_positions",
    "live_orders",
    "bot_config",
    "bot_signal_state",
    "audit_log",
    "bot_status",
}


def test_all_expected_tables_registered():
    assert set(Base.metadata.tables.keys()) == EXPECTED_TABLES


def test_primary_keys_are_uuid():
    for table in Base.metadata.tables.values():
        pk_cols = list(table.primary_key.columns)
        assert len(pk_cols) == 1
        assert pk_cols[0].name == "id"


def _money_columns(table_name, names):
    table = Base.metadata.tables[table_name]
    for name in names:
        col = table.columns[name]
        assert isinstance(col.type, Numeric), f"{table_name}.{name} must be NUMERIC, got {col.type}"


def test_money_and_price_columns_are_numeric_not_float():
    _money_columns(
        "backtest_runs",
        ["sharpe_ratio", "max_drawdown_pct", "win_rate_pct", "profit_factor", "cagr_pct"],
    )
    _money_columns("backtest_trades", ["entry_price", "exit_price", "pnl"])
    _money_columns("equity_curve_points", ["equity"])
    _money_columns(
        "paper_positions",
        ["quantity", "avg_entry_price", "realized_pnl", "unrealized_pnl"],
    )
    _money_columns("paper_orders", ["quantity", "simulated_fill_price"])
    _money_columns(
        "bot_config", ["virtual_capital", "max_position_size", "daily_loss_limit"]
    )


def test_foreign_keys_match_er_diagram():
    fks = {
        "backtest_runs": {"strategy_id": "strategies.id", "instrument_id": "instruments.id"},
        "backtest_trades": {"backtest_run_id": "backtest_runs.id"},
        "equity_curve_points": {"backtest_run_id": "backtest_runs.id"},
        "paper_positions": {"strategy_id": "strategies.id", "instrument_id": "instruments.id"},
        "paper_orders": {"paper_position_id": "paper_positions.id"},
        "bot_config": {"strategy_id": "strategies.id", "instrument_id": "instruments.id"},
    }
    for table_name, cols in fks.items():
        table = Base.metadata.tables[table_name]
        for col_name, target in cols.items():
            col = table.columns[col_name]
            assert len(col.foreign_keys) == 1
            fk = next(iter(col.foreign_keys))
            assert fk.target_fullname == target


def test_timestamp_columns_are_timezone_aware():
    from sqlalchemy import DateTime

    checks = {
        "backtest_runs": ["started_at", "period_start", "period_end"],
        "backtest_trades": ["entered_at", "exited_at"],
        "equity_curve_points": ["ts"],
        "paper_positions": ["opened_at", "closed_at"],
        "paper_orders": ["filled_at"],
        "bot_config": ["updated_at"],
        "audit_log": ["ts"],
    }
    for table_name, cols in checks.items():
        table = Base.metadata.tables[table_name]
        for col_name in cols:
            col = table.columns[col_name]
            assert isinstance(col.type, DateTime)
            assert col.type.timezone is True, f"{table_name}.{col_name} must be timestamptz"


def test_instrument_and_strategy_columns():
    instruments = Base.metadata.tables["instruments"]
    assert set(["symbol", "exchange_segment", "security_id", "name"]).issubset(
        instruments.columns.keys()
    )
    strategies = Base.metadata.tables["strategies"]
    assert set(["name", "version", "params"]).issubset(strategies.columns.keys())


def test_orm_classes_instantiate_with_expected_attrs():
    instrument = Instrument(
        id=uuid.uuid4(),
        symbol="GOLDM",
        exchange_segment="MCX_COMM",
        security_id="12345",
        name="Gold Mini",
    )
    assert instrument.symbol == "GOLDM"

    strategy = Strategy(id=uuid.uuid4(), name="sma_crossover", version="1.0", params={"fast": 5})
    assert strategy.params == {"fast": 5}
