"""Add portfolio_backtest_runs / portfolio_equity_curve_points / portfolio_rebalance_holdings

For the cross-sectional small-cap/mid-cap momentum(+quality) research
backtest (bot/research/smallcap_momentum/) -- distinct from the existing
backtest_runs (always one strategy x one instrument). Not linked to
strategies/instruments: a portfolio run holds a rotating basket of many
stocks, identified by `universe`/`variant` instead. See
docs/smallcap-momentum-research.md and
docs/smallcap-momentum-backtest-results.md.

Revision ID: 0013_portfolio_backtest
Revises: 0012_rejection_throttle
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_portfolio_backtest"
down_revision = "0012_rejection_throttle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolio_backtest_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("universe", sa.Text(), nullable=False),
        sa.Column("variant", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("top_n", sa.Integer(), nullable=False),
        sa.Column("initial_capital", sa.Numeric(), nullable=False),
        sa.Column("final_equity", sa.Numeric(), nullable=False),
        sa.Column("rebalance_count", sa.Integer(), nullable=False),
        sa.Column("sharpe_ratio", sa.Numeric(), nullable=True),
        sa.Column("max_drawdown_pct", sa.Numeric(), nullable=True),
        sa.Column("win_rate_pct", sa.Numeric(), nullable=True),
        sa.Column("cagr_pct", sa.Numeric(), nullable=True),
        sa.Column("quality_coverage_pct", sa.Numeric(), nullable=True),
    )
    op.create_table(
        "portfolio_equity_curve_points",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "portfolio_backtest_run_id",
            sa.Uuid(),
            sa.ForeignKey("portfolio_backtest_runs.id"),
            nullable=False,
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("equity", sa.Numeric(), nullable=False),
    )
    op.create_index(
        "ix_portfolio_equity_curve_points_run_id",
        "portfolio_equity_curve_points",
        ["portfolio_backtest_run_id"],
    )
    op.create_table(
        "portfolio_rebalance_holdings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "portfolio_backtest_run_id",
            sa.Uuid(),
            sa.ForeignKey("portfolio_backtest_runs.id"),
            nullable=False,
        ),
        sa.Column("rebalance_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("weight", sa.Numeric(), nullable=False),
        sa.Column("composite_score", sa.Numeric(), nullable=True),
    )
    op.create_index(
        "ix_portfolio_rebalance_holdings_run_id",
        "portfolio_rebalance_holdings",
        ["portfolio_backtest_run_id"],
    )


def downgrade() -> None:
    op.drop_table("portfolio_rebalance_holdings")
    op.drop_table("portfolio_equity_curve_points")
    op.drop_table("portfolio_backtest_runs")
