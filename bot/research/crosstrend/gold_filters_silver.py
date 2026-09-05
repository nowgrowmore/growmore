"""Does Gold Mini's trend improve Silver Mini's trades?

    python -m research.crosstrend.gold_filters_silver

Silver Mini is the noisier, higher-beta expression of the same macro as Gold
Mini. The hypothesis is that silver's losing trades cluster where gold was NOT
trending -- whipsaw a calmer companion would have vetoed. If so, a companion
filter should cut silver's drawdown without proportionally cutting its return.

This is a GATE, not a feature. Supporting it in production means changing
Strategy.on_bar to accept a companion bar, which touches base.py and all three
engines. That cost is paid only if the effect is real and large enough to
survive the trials count.

Both directions are run, and every pair of instruments is offered, because a
filter that only works on the one pair you hoped for is a coincidence.
"""
from __future__ import annotations

import argparse
import sys

from growmore_bot.backtest.engine import BacktestEngine
from growmore_bot.backtest.metrics import (
    cagr_pct,
    max_drawdown_pct,
    profit_factor,
    sharpe_ratio,
)
from growmore_bot.backtest.run_all import capital_for_run
from growmore_bot.costs import DEFAULT_COST_MODEL
from growmore_bot.strategies.registry import build_strategy
from research.crosstrend.companion import CompanionFilteredStrategy, trend_states
from research.dailydata import cache
from research.dailydata.fetch import load_meta
from research.dailydata.runner import risk_managed

MACD_FAST = {"fast_period": 5, "slow_period": 13, "signal_period": 5}
MACD_SLOW = {"fast_period": 12, "slow_period": 26, "signal_period": 9}

TARGETS = [
    ("rm macd5-13-5", "risk_managed", risk_managed("macd_trend", MACD_FAST, 2.0, 3.0)),
    ("rm ensemble-agree3", "risk_managed",
     risk_managed("ensemble_trend", {"min_agreement": 3}, 2.0, 3.0)),
    ("macd5-13-5 (bare)", "macd_trend", MACD_FAST),
]

#: How the companion's opinion is formed. Slow deliberately -- the point of a
#: companion is that it is calmer than the instrument being filtered.
COMPANION_SIGNALS = [
    ("macd12-26-9", "macd_trend", MACD_SLOW),
    ("macd5-13-5", "macd_trend", MACD_FAST),
    ("ensemble-agree3", "ensemble_trend", {"min_agreement": 3}),
]


def _measure(symbol, bars, strategy, meta):
    info = meta[symbol]
    capital = capital_for_run(
        "notional", first_close=float(bars[0].close), lot_size=info["lot_size"],
        flat_capital=500_000.0, target_leverage=1.0,
    )
    engine = BacktestEngine(
        strategy=strategy, initial_capital=capital, lot_size=info["lot_size"],
        cost_model=DEFAULT_COST_MODEL, tick_size=float(info["tick_size"] or 0.0),
    )
    result = engine.run(list(bars))
    equity = [p.equity for p in result.equity_curve]
    returns = [(b / a - 1) for a, b in zip(equity, equity[1:]) if a != 0]
    closed = [t.pnl for t in result.trades if t.pnl is not None]
    years = max((bars[-1].timestamp - bars[0].timestamp).days / 365.25, 0.0)
    pf = profit_factor(closed)
    return {
        "trades": len(closed),
        "cagr": cagr_pct(capital, result.final_equity, years) if years else 0.0,
        "sharpe": sharpe_ratio(returns),
        "dd": max_drawdown_pct(equity),
        "pf": None if pf == float("inf") else pf,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", nargs="*",
                        default=["SILVERM:GOLDM", "GOLDM:SILVERM", "COPPER:GOLDM",
                                 "NICKEL:COPPER", "ZINCMINI:COPPER"],
                        help="TARGET:COMPANION")
    args = parser.parse_args(argv)
    meta = load_meta()

    print(f"{'target':<9} {'companion':<9} {'signal':<16} {'variant':<20} "
          f"{'trds':>5} {'CAGR':>7} {'Sharpe':>7} {'MaxDD':>7} {'veto':>5}")
    print("-" * 96)

    improvements = []
    for pair in args.pairs:
        target, companion = pair.split(":")
        try:
            t_bars = cache.load(target)
            c_bars = cache.load(companion)
        except FileNotFoundError as exc:
            print(f"  skip {pair}: {exc}", file=sys.stderr)
            continue

        # Align: the filter can only speak for days both instruments traded.
        common = {b.timestamp.date() for b in c_bars}
        t_bars = [b for b in t_bars if b.timestamp.date() >= min(common)]

        for label, name, params in TARGETS:
            base = _measure(target, t_bars, build_strategy(name, params), meta)
            print(f"{target:<9} {'--':<9} {'(unfiltered)':<16} {label:<20} "
                  f"{base['trades']:>5} {base['cagr']:>6.1f}% {base['sharpe']:>7.2f} "
                  f"{base['dd']:>6.1f}% {'--':>5}")

            for c_label, c_name, c_params in COMPANION_SIGNALS:
                states = trend_states(c_bars, c_name, c_params)
                wrapped = CompanionFilteredStrategy(build_strategy(name, params), states)
                got = _measure(target, t_bars, wrapped, meta)
                delta = got["sharpe"] - base["sharpe"]
                improvements.append((target, companion, c_label, label, delta))
                print(f"{target:<9} {companion:<9} {c_label:<16} {label:<20} "
                      f"{got['trades']:>5} {got['cagr']:>6.1f}% {got['sharpe']:>7.2f} "
                      f"{got['dd']:>6.1f}% {wrapped.vetoed:>5}   "
                      f"({delta:+.2f} Sharpe)")
            print()

    better = [i for i in improvements if i[4] > 0]
    print("=" * 96)
    print(f"the companion filter improved Sharpe in {len(better)} of {len(improvements)} "
          f"(target x companion-signal x variant) combinations")
    if improvements:
        mean = sum(i[4] for i in improvements) / len(improvements)
        print(f"mean Sharpe change: {mean:+.3f}")
        best = max(improvements, key=lambda i: i[4])
        print(f"best: {best[0]} filtered by {best[1]} {best[2]} on {best[3]}: {best[4]:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
