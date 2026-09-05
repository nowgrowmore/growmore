"""Run a strategy variant over cached daily bars, with no database at all.

`growmore_bot.backtest.run_all` fetches from Dhan and writes every run to
Postgres. That is right for the production sweep and wrong for research: it is
slow, it needs a live token, it is not reproducible (the series moves as
contracts roll), and it contends with whatever else is writing to Neon.

This runs the same `BacktestEngine`, with the same cost model and the same
per-instrument capital rule, against the parquet cache -- so a research number
and a sweep number are comparable, but a research run costs nothing and can be
repeated exactly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional, Sequence

from growmore_bot.backtest.engine import BacktestEngine
from growmore_bot.backtest.metrics import (
    cagr_pct,
    max_drawdown_pct,
    profit_factor,
    sharpe_ratio,
    win_rate_pct,
)
from growmore_bot.backtest.run_all import capital_for_run
from growmore_bot.costs import DEFAULT_COST_MODEL, CostModel
from growmore_bot.strategies.registry import build_strategy
from research.dailydata import cache
from research.dailydata.fetch import load_meta


@dataclass(frozen=True)
class RunResult:
    symbol: str
    label: str
    trades: int
    cagr_pct: float
    sharpe: float
    max_drawdown_pct: float
    profit_factor: Optional[float]
    win_rate_pct: float
    final_equity: float
    initial_capital: float
    total_cost: float
    equity_curve: list[float]
    returns: list[float]
    exit_reasons: dict[str, int]

    def as_row(self) -> str:
        pf = "  inf" if self.profit_factor is None else f"{self.profit_factor:5.2f}"
        return (
            f"{self.symbol:<10} {self.label:<34} {self.trades:>4} "
            f"{self.cagr_pct:>7.1f}% {self.sharpe:>6.2f} {self.max_drawdown_pct:>7.1f}% {pf}"
        )


def run_variant(
    symbol: str,
    strategy_name: str,
    params: dict,
    label: str,
    bars: Optional[Sequence[Any]] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    with_costs: bool = True,
    capital_mode: str = "notional",
    target_leverage: float = 1.0,
    meta: Optional[dict] = None,
    evaluate_from: int = 0,
    cost_model: Optional[CostModel] = DEFAULT_COST_MODEL,
    allow_shorts: bool = False,
) -> RunResult:
    """Backtest one (symbol, strategy, params) over the cached daily series.

    `evaluate_from` is the walk-forward hook. A streaming strategy needs warm
    bars before its indicators mean anything, so an out-of-sample segment is
    run as [warm-up bars] + [test bars] in one pass and only the tail is
    scored. The strategy is allowed to SEE the warm-up bars (they are its own
    past); what it must never see is a bar that was used to select it, which
    is the caller's job to arrange. Setting this to 0 scores everything.
    """
    meta = meta if meta is not None else load_meta()
    info = meta[symbol]
    if bars is None:
        bars = cache.load(symbol, from_date=from_date, to_date=to_date)
    if not bars:
        raise ValueError(f"No bars for {symbol} in the requested window")

    initial_capital = capital_for_run(
        capital_mode,
        first_close=float(bars[0].close),
        lot_size=info["lot_size"],
        flat_capital=500_000.0,
        target_leverage=target_leverage,
    )
    engine = BacktestEngine(
        strategy=build_strategy(strategy_name, params),
        initial_capital=initial_capital,
        lot_size=info["lot_size"],
        cost_model=cost_model if with_costs else None,
        tick_size=float(info["tick_size"] or 0.0),
        allow_shorts=allow_shorts,
    )
    result = engine.run(list(bars))

    full_equity = [p.equity for p in result.equity_curve]
    equity = full_equity[evaluate_from:]
    returns = [(b / a - 1) for a, b in zip(equity, equity[1:]) if a != 0]

    # A trade is scored in the window it was ENTERED in, so a position carried
    # across the boundary belongs to the warm-up window, not this one.
    scored_from = bars[evaluate_from].timestamp if evaluate_from < len(bars) else None
    scored_trades = [
        t for t in result.trades
        if t.pnl is not None and (scored_from is None or t.entered_at >= scored_from)
    ]
    closed = [t.pnl for t in scored_trades]
    span = bars[evaluate_from:] or bars
    years = max((span[-1].timestamp - span[0].timestamp).days / 365.25, 0.0)
    pf = profit_factor(closed)

    # The capital the SCORED window actually started with -- for a walk-forward
    # segment that is the equity carried in, not the original stake.
    base_capital = equity[0] if equity else initial_capital

    reasons: dict[str, int] = {}
    for t in scored_trades:
        if t.exit_price is not None:
            reasons[t.exit_reason or "none"] = reasons.get(t.exit_reason or "none", 0) + 1

    return RunResult(
        symbol=symbol,
        label=label,
        trades=len(closed),
        cagr_pct=cagr_pct(base_capital, result.final_equity, years) if years else 0.0,
        sharpe=sharpe_ratio(returns),
        max_drawdown_pct=max_drawdown_pct(equity),
        profit_factor=None if pf == float("inf") else pf,
        win_rate_pct=win_rate_pct(closed),
        final_equity=result.final_equity,
        initial_capital=base_capital,
        total_cost=result.total_transaction_cost,
        equity_curve=equity,
        returns=returns,
        exit_reasons=reasons,
    )


HEADER = (
    f"{'inst':<10} {'variant':<34} {'trds':>4} {'CAGR':>8} {'Sharpe':>6} {'MaxDD':>8} {'PF':>5}"
)


def risk_managed(inner: str, inner_params: dict, stop: float, trail: Optional[float],
                 max_bars: Optional[int] = None) -> dict:
    """Shorthand for the params shape build_risk_managed expects."""
    params: dict = {
        "inner_strategy": inner,
        "inner_params": inner_params,
        "atr_period": 14,
        "initial_stop_atr": stop,
        "trail_atr": trail,
    }
    if max_bars is not None:
        params["max_bars_held"] = max_bars
    return params
