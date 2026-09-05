"""CLI: run a strategy x parameter x instrument sweep over available history,
persist results, and print a ranked summary.

Ranking: primary key is profit factor (descending) -- unlike Sharpe/max
drawdown, it's a ratio of a run's own trade outcomes and stays comparable
across commodities at very different price levels and lot sizes (see
docs/technical-debt.md for the scaling bug this sidesteps). `max_drawdown_pct`
and trade count are guardrails, not sort keys -- anything breaching
`--max-drawdown-guardrail` (default 50%) or below `--min-trade-count`
(default 15) is flagged in the printed table rather than silently dropped, so
a human decides what's actually actionable.

Each (strategy family, parameter variant) gets its own `Strategy` row, keyed
by (name, version) -- version encodes the parameters in a short readable
label (e.g. "fast5-slow20"). Re-running with the same variant reuses that
row; a different variant of the same strategy family gets its own row rather
than silently overwriting a stale one's params (the bug this replaced).

Usage:
    python -m growmore_bot.backtest.run_all --from-date 2021-01-01 --to-date 2026-09-01
"""
from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
from typing import Any, Callable, Optional, Sequence

from growmore_bot.backtest.engine import BacktestEngine
from growmore_bot.broker.dhan_client import DhanClient
from growmore_bot.config import Settings
from growmore_bot.costs import DEFAULT_COST_MODEL
from growmore_bot.persistence.db import session_scope
from growmore_bot.persistence.models import BacktestRun, BacktestTrade, Instrument
from growmore_bot.persistence.models import Strategy as StrategyRow
from growmore_bot.risk.sizing import notional_per_lot
from growmore_bot.risk.wrapper import build_risk_managed
from growmore_bot.strategies.base import Strategy
from growmore_bot.strategies.bollinger_reversion import BollingerReversionStrategy
from growmore_bot.strategies.donchian_breakout import DonchianBreakoutStrategy
from growmore_bot.strategies.ensemble_trend import EnsembleTrendStrategy
from growmore_bot.strategies.macd_trend import MacdTrendStrategy
from growmore_bot.strategies.regime_switch import RegimeSwitchStrategy
from growmore_bot.strategies.rsi_mean_reversion import RsiMeanReversionStrategy
from growmore_bot.strategies.sma_crossover import SmaCrossoverStrategy

DEFAULT_MAX_DRAWDOWN_GUARDRAIL_PCT = 50.0
DEFAULT_MIN_TRADE_COUNT = 15
DEFAULT_TARGET_LEVERAGE = 1.0


def capital_for_run(
    mode: str,
    first_close: float,
    lot_size: int,
    flat_capital: float,
    target_leverage: float = DEFAULT_TARGET_LEVERAGE,
) -> float:
    """How much capital this instrument's backtest is measured against.

    `"flat"` is the original behaviour: one figure for every instrument. That
    is what makes the CAGR column incomparable, because a Copper lot
    (~Rs 34 lakh) against Rs 5 lakh is ~6.9x leverage while a Crude Oil Mini
    lot (~Rs 0.86 lakh) is ~0.17x -- a 40x spread in how much risk "1 lot"
    represents. Retained so old runs can be reproduced exactly.

    `"notional"` (the default) sets capital to one lot's own notional divided
    by `target_leverage`, so every instrument starts the backtest at the SAME
    leverage and their CAGRs finally mean the same thing.

    The price used is deliberately the FIRST bar's close, not the last or the
    mean: you capitalise an account at the start of the period, and using any
    later price would be lookahead.
    """
    if mode == "flat":
        return float(flat_capital)
    if mode != "notional":
        raise ValueError(f"unknown capital mode {mode!r}")
    if target_leverage <= 0:
        raise ValueError("target_leverage must be positive")
    return notional_per_lot(first_close, lot_size) / target_leverage


@dataclass
class RunSummary:
    strategy: str
    version: str
    instrument: str
    trade_count: int
    profit_factor: float
    win_rate_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    cagr_pct: float = 0.0
    initial_capital: float = 0.0
    flagged_drawdown: bool = field(default=False)
    flagged_thin_sample: bool = field(default=False)


def rank_results(
    results: Sequence[RunSummary],
    max_drawdown_guardrail_pct: float,
    min_trade_count: int,
) -> list[RunSummary]:
    """Return results sorted by profit factor descending, flagging drawdown
    breaches and thin samples. Flagging never removes a result from the list
    -- it's guardrail metadata for the human reading the output, not an
    automated exclusion.
    """
    flagged = [
        RunSummary(
            strategy=r.strategy,
            version=r.version,
            instrument=r.instrument,
            trade_count=r.trade_count,
            profit_factor=r.profit_factor,
            win_rate_pct=r.win_rate_pct,
            sharpe_ratio=r.sharpe_ratio,
            max_drawdown_pct=r.max_drawdown_pct,
            cagr_pct=r.cagr_pct,
            initial_capital=r.initial_capital,
            flagged_drawdown=r.max_drawdown_pct > max_drawdown_guardrail_pct,
            flagged_thin_sample=r.trade_count < min_trade_count,
        )
        for r in results
    ]
    return sorted(flagged, key=lambda r: r.profit_factor, reverse=True)


def profit_factor_for_ranking(stored_value: float | None) -> float:
    """`BacktestRun.profit_factor` is stored as None specifically to mean
    "infinite" (all winning trades, zero losses) -- see
    BacktestEngine.run_and_persist. Mapping None to 0.0 here would rank a
    zero-loss run as the WORST possible result instead of the best (a real
    bug found while reviewing this sweep's first real output: several 100%
    win-rate rows showed profit_factor=0.00).
    """
    return float("inf") if stored_value is None else float(stored_value)


def delete_existing_runs(session: Any, strategy_id: Any, instrument_id: Any) -> None:
    """Delete any previously-persisted `BacktestRun` rows for this exact
    (strategy_id, instrument_id) pairing before persisting a fresh one.

    Without this, re-running the sweep after a code change -- a real,
    repeated occurrence, see docs/backtest-results.md's revision history --
    piles up duplicate rows that look identical on the dashboard (same
    strategy/version/params/instrument) except for their real result
    numbers and `started_at`. Found live 2026-09-05: the Backtests/Rankings
    page had no way to tell these apart or collapse them, so the same
    strategy+instrument pairing showed up several times with different
    Sharpe/CAGR each time. Cascades to the run's own `BacktestTrade`/
    `EquityCurvePoint` rows via the ORM relationship's
    `cascade="all, delete-orphan"` (see persistence/models.py).
    """
    old_runs = (
        session.query(BacktestRun)
        .filter_by(strategy_id=strategy_id, instrument_id=instrument_id)
        .all()
    )
    for old_run in old_runs:
        session.delete(old_run)


def build_strategy_grid() -> list[tuple[str, str, dict, Callable[[], Strategy]]]:
    """The parameter sweep grid: (strategy name, version label, params,
    factory). The last element is a FACTORY, not an instance, deliberately.

    Kept small and reasoned (14 variants total across 5 strategy families),
    not exhaustive -- see docs/pending-actions.md for why an unbounded grid
    search would be a multiple-comparisons trap rather than useful analysis.

    Regression (independent code review 2026-09-04): this used to return one
    already-constructed instance per variant, which `main()` then reused for
    EVERY instrument in the sweep. Every strategy here is stateful (rolling
    close deques, seeded EMAs, previous-crossing flags, ADX's previous bar),
    so each instrument after the first began with the previous commodity's
    price history still loaded -- Gold Mini's ~70,000-level closes sitting in
    the window as Copper's ~700-level bars arrived, fabricating crossings and
    breakouts and corrupting the rankings that decide where real money goes.
    A fresh instance per (instrument, variant) is the only safe contract.
    """
    grid: list[tuple[str, str, dict, Callable[[], Strategy]]] = []

    for fast, slow in [(5, 20), (10, 30), (10, 50), (20, 50)]:
        params: dict[str, Any] = {"fast_period": fast, "slow_period": slow}
        grid.append(
            (
                "sma_crossover",
                f"fast{fast}-slow{slow}",
                params,
                partial(SmaCrossoverStrategy, **params),
            )
        )

    for period in [10, 20, 55]:
        params = {"period": period}
        grid.append(
            (
                "donchian_breakout",
                f"period{period}",
                params,
                partial(DonchianBreakoutStrategy, **params),
            )
        )

    for period, oversold, overbought in [(14, 30, 70), (14, 20, 80), (7, 30, 70)]:
        params = {"period": period, "oversold": oversold, "overbought": overbought}
        grid.append(
            (
                "rsi_mean_reversion",
                f"period{period}-{oversold}-{overbought}",
                params,
                partial(RsiMeanReversionStrategy, **params),
            )
        )

    for fast, slow, sig in [(12, 26, 9), (5, 13, 5)]:
        params = {"fast_period": fast, "slow_period": slow, "signal_period": sig}
        grid.append(
            (
                "macd_trend",
                f"fast{fast}-slow{slow}-sig{sig}",
                params,
                partial(MacdTrendStrategy, **params),
            )
        )

    for period, num_std in [(20, 2.0), (20, 2.5)]:
        params = {"period": period, "num_std": num_std}
        grid.append(
            (
                "bollinger_reversion",
                f"period{period}-k{num_std}",
                params,
                partial(BollingerReversionStrategy, **params),
            )
        )

    # ADX-gated regime-switch: routes to MACD when trending, a mean-reversion
    # strategy when ranging. Crosses the two MACD pairings already proven on
    # Gold Mini (see docs/backtest-results.md) with both ranging-mode options
    # -- real data decides which ranging style is actually better, not a
    # guess. See docs/goldmini-regime-switch-results.md for the results.
    macd_variants = [(12, 26, 9), (5, 13, 5)]
    ranging_variants = [
        ("rsi", {"period": 14, "oversold": 30, "overbought": 70}),
        ("vwap_ema", {"vwap_period": 20, "ema_fast": 8, "ema_slow": 21}),
    ]
    for fast, slow, sig in macd_variants:
        for ranging_name, ranging_params in ranging_variants:
            params = {
                "ranging_strategy": ranging_name,
                "macd_params": {"fast_period": fast, "slow_period": slow, "signal_period": sig},
                "ranging_params": ranging_params,
            }
            version = f"adx14-macd{fast}{slow}{sig}-{ranging_name}"
            grid.append(
                ("regime_switch", version, params, partial(RegimeSwitchStrategy, **params))
            )

    # Multi-lookback trend ensemble. Only TWO variants deliberately: the
    # deflated-Sharpe result (docs/technical-debt.md) says every extra grid
    # entry raises the bar the eventual winner has to clear, and the whole
    # point of an ensemble is to stop choosing a lookback -- adding a dozen
    # ensemble configurations would reintroduce the selection problem it
    # exists to avoid.
    for min_agreement in (3, 4):
        params = {"min_agreement": min_agreement}
        grid.append((
            "ensemble_trend",
            f"macd5-agree{min_agreement}",
            params,
            partial(EnsembleTrendStrategy, **params),
        ))

    # Risk-managed variants: the SAME entry rules as above, plus an ATR stop
    # and a Chandelier trail. Donchian is the deliberate first subject --
    # a breakout system without a stop is the textbook case of a strategy
    # that only works with one, so if the risk layer doesn't lift Donchian
    # the risk layer itself is suspect. MACD 5/13/5 is included because it
    # is the incumbent best pick and the comparison has to be fair.
    risk_variants: list[tuple[str, dict, float, Optional[float]]] = [
        ("donchian_breakout", {"period": 20}, 2.0, 3.0),
        ("donchian_breakout", {"period": 20}, 2.0, None),
        ("donchian_breakout", {"period": 55}, 2.0, 3.0),
        ("macd_trend", {"fast_period": 5, "slow_period": 13, "signal_period": 5}, 2.0, 3.0),
        ("macd_trend", {"fast_period": 5, "slow_period": 13, "signal_period": 5}, 3.0, None),
        ("macd_trend", {"fast_period": 12, "slow_period": 26, "signal_period": 9}, 2.0, 3.0),
    ]
    # The ensemble deserves the stops too -- it is the combination, not
    # either piece alone, that the evidence so far points at.
    ensemble_risk = {
        "inner_strategy": "ensemble_trend",
        "inner_params": {"min_agreement": 3},
        "atr_period": 14,
        "initial_stop_atr": 2.0,
        "trail_atr": 3.0,
    }
    grid.append((
        "risk_managed", "ensemble_trend-agree3-stop2-trail3",
        ensemble_risk, partial(build_risk_managed, ensemble_risk),
    ))
    for inner_name, inner_params, stop_atr, trail in risk_variants:
        params = {
            "inner_strategy": inner_name,
            "inner_params": inner_params,
            "atr_period": 14,
            "initial_stop_atr": stop_atr,
            "trail_atr": trail,
        }
        tag = "-".join(str(v) for v in inner_params.values())
        trail_tag = f"trail{trail:g}" if trail is not None else "notrail"
        version = f"{inner_name}-{tag}-stop{stop_atr:g}-{trail_tag}"
        grid.append(("risk_managed", version, params, partial(build_risk_managed, params)))

    return grid


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--to-date", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--max-drawdown-guardrail",
        type=float,
        default=DEFAULT_MAX_DRAWDOWN_GUARDRAIL_PCT,
        help="Flag any result whose max_drawdown_pct exceeds this (default 50).",
    )
    parser.add_argument(
        "--capital-mode",
        choices=["notional", "flat"],
        default="notional",
        help="How to capitalise each instrument's backtest. 'notional' (default) uses one lot's "
        "own notional so every instrument runs at the same leverage and CAGRs are comparable; "
        "'flat' reproduces the old behaviour of one figure for all of them.",
    )
    parser.add_argument(
        "--target-leverage",
        type=float,
        default=DEFAULT_TARGET_LEVERAGE,
        help="Leverage each instrument is capitalised to under --capital-mode=notional "
        "(default 1.0, i.e. one lot exactly equals the account).",
    )
    parser.add_argument(
        "--allow-shorts",
        action="store_true",
        help="Let a SELL while flat open a short position. Off by default so results stay "
        "comparable with every previously stored run.",
    )
    parser.add_argument(
        "--no-costs",
        action="store_true",
        help="Skip the MCX transaction-cost/slippage model (reproduces pre-2026-09 results).",
    )
    parser.add_argument(
        "--min-trade-count",
        type=int,
        default=DEFAULT_MIN_TRADE_COUNT,
        help="Flag any result with fewer closed trades than this (default 15).",
    )
    args = parser.parse_args(argv)

    settings = Settings()
    client = DhanClient(client_id=settings.dhan_client_id, access_token=settings.dhan_access_token)
    client.refresh_access_token_if_needed()

    grid = build_strategy_grid()
    summaries: list[RunSummary] = []

    with session_scope() as session:
        instruments = session.query(Instrument).all()
        if not instruments:
            print(
                "No instruments configured in the `instruments` table yet -- "
                "add rows with real Dhan security ids before running backtests.",
                file=sys.stderr,
            )
            return 1

        for instrument in instruments:
            bars = client.get_historical_ohlc(
                instrument, from_date=args.from_date, to_date=args.to_date, interval="day"
            )
            if not bars:
                continue

            for strategy_name, version, params, strategy_factory in grid:
                # A FRESH instance per (instrument, variant) -- strategies are
                # stateful, so sharing one across instruments would carry the
                # previous commodity's price history into this one's window.
                strategy_impl = strategy_factory()
                strategy_row = (
                    session.query(StrategyRow)
                    .filter_by(name=strategy_name, version=version)
                    .one_or_none()
                )
                if strategy_row is None:
                    strategy_row = StrategyRow(
                        id=uuid.uuid4(), name=strategy_name, version=version, params=params
                    )
                    session.add(strategy_row)
                    session.flush()

                initial_capital = capital_for_run(
                    args.capital_mode,
                    first_close=float(bars[0].close),
                    lot_size=instrument.lot_size,
                    flat_capital=settings.default_virtual_capital,
                    target_leverage=args.target_leverage,
                )
                engine = BacktestEngine(
                    strategy=strategy_impl,
                    initial_capital=initial_capital,
                    lot_size=instrument.lot_size,
                    cost_model=None if args.no_costs else DEFAULT_COST_MODEL,
                    tick_size=float(instrument.tick_size or 0.0),
                    allow_shorts=args.allow_shorts,
                )
                delete_existing_runs(session, strategy_row.id, instrument.id)
                run_row = engine.run_and_persist(
                    bars,
                    session=session,
                    strategy_id=strategy_row.id,
                    instrument_id=instrument.id,
                    started_at=datetime.now(timezone.utc),
                )
                session.flush()
                trade_count = (
                    session.query(BacktestTrade)
                    .filter_by(backtest_run_id=run_row.id)
                    .filter(BacktestTrade.pnl.isnot(None))
                    .count()
                )
                summaries.append(
                    RunSummary(
                        strategy=strategy_name,
                        version=version,
                        instrument=instrument.symbol,
                        trade_count=trade_count,
                        profit_factor=profit_factor_for_ranking(run_row.profit_factor),
                        win_rate_pct=float(run_row.win_rate_pct or 0),
                        sharpe_ratio=float(run_row.sharpe_ratio or 0),
                        max_drawdown_pct=float(run_row.max_drawdown_pct or 0),
                        cagr_pct=float(run_row.cagr_pct or 0),
                        initial_capital=initial_capital,
                    )
                )

    ranked = rank_results(
        summaries,
        max_drawdown_guardrail_pct=args.max_drawdown_guardrail,
        min_trade_count=args.min_trade_count,
    )
    print(
        f"{'strategy':<20}{'version':<22}{'instrument':<15}{'trades':>7}"
        f"{'pf':>8}{'win%':>8}{'sharpe':>8}{'maxdd%':>8}{'cagr%':>8}{'capital':>12}  flags"
    )
    for r in ranked:
        flags = " ".join(
            f for f in [
                "DRAWDOWN!" if r.flagged_drawdown else "",
                "THIN!" if r.flagged_thin_sample else "",
            ]
            if f
        )
        print(
            f"{r.strategy:<20}{r.version:<22}{r.instrument:<15}{r.trade_count:>7}"
            f"{r.profit_factor:>8.2f}{r.win_rate_pct:>8.1f}{r.sharpe_ratio:>8.2f}"
            f"{r.max_drawdown_pct:>8.2f}{r.cagr_pct:>8.1f}{r.initial_capital:>12,.0f}  {flags}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "RunSummary",
    "build_strategy_grid",
    "capital_for_run",
    "rank_results",
    "profit_factor_for_ranking",
    "main",
]
