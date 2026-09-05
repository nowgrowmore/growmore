"""Score every variant as a FIXED strategy over the walk-forward test windows.

    python -m research.validation.oos_pairs --symbols GOLDM SILVERM

`walk_forward_run` answers "does re-selecting help?". This answers a different
question: "if you had simply run THIS variant, what would you have got in the
out-of-sample windows?" -- which is what you need to judge an idea rather than
a selector.

Reported as MATCHED PAIRS (buffer vs no buffer, time stop vs none, vol filter
vs none, each stop multiple vs 2.0/3.0), because a paired comparison is not a
search: the two members of a pair differ by exactly one decision, so the
difference is attributable, and no pair is being cherry-picked from a ranking.
Picking the best row of the table WOULD be a search, and would need to be
counted against the trials budget. Don't.

Same out-of-sample bars as walk_forward_run, same warm-up discipline: each
fold is run as [train bars][test bars] in one pass and only the test tail is
scored, so indicators are warm and no selection ever saw a test bar.
"""
from __future__ import annotations

import argparse
import sys

from growmore_bot.backtest.metrics import max_drawdown_pct, sharpe_ratio
from growmore_bot.backtest.walk_forward import (
    DEFAULT_STEP,
    DEFAULT_TEST,
    DEFAULT_TRAIN,
    make_folds,
)
from research.dailydata import cache
from research.dailydata.fetch import load_meta
from research.dailydata.runner import run_variant
from research.validation.walk_forward_run import BASE_GRID

#: (pair label, baseline variant, treatment variant) -- one decision apart.
PAIRS = [
    ("4b no-trade buffer 0.10", "rm-macd5-13-5", "rm-macd5-13-5-buf0.1"),
    ("4b no-trade buffer 0.25", "rm-macd5-13-5", "rm-macd5-13-5-buf0.25"),
    ("4b buffer 0.25 on slow", "rm-macd12-26-9", "rm-macd12-26-9-buf0.25"),
    ("4c vol filter p90", "rm-macd5-13-5", "vol90-rm-macd5-13-5"),
    ("4c vol filter p80", "rm-macd5-13-5", "vol80-rm-macd5-13-5"),
    ("4c vol filter p90 (ens)", "rm-ensemble-agree3", "vol90-rm-ensemble"),
    ("4d time stop 30 bars", "rm-macd5-13-5", "rm-macd5-13-5-time30"),
    ("4d time stop 60 bars", "rm-macd5-13-5", "rm-macd5-13-5-time60"),
    ("4d time stop 60 (ens)", "rm-ensemble-agree3", "rm-ensemble-time60"),
    ("4e stop 1.5 / trail 2", "rm-macd5-13-5", "rm-macd5-13-5-stop1.5-trail2"),
    ("4e stop 3 / trail 4", "rm-macd5-13-5", "rm-macd5-13-5-stop3-trail4"),
    ("4e stop 1.5/2 (ens)", "rm-ensemble-agree3", "rm-ensemble-stop1.5-trail2"),
    ("4e stop 3/4 (ens)", "rm-ensemble-agree3", "rm-ensemble-stop3-trail4"),
    ("4a slow EMA 112 vs fast", "macd5-13-5", "ema112"),
    ("4a slow EMA 112 + stops", "rm-macd5-13-5", "rm-ema112"),
]


def oos_series(symbol, name, params, label, bars, folds, meta):
    """Concatenate this variant's test-window returns across every fold."""
    returns, trades = [], 0
    for fold in folds:
        window = bars[fold.train_start : fold.test_end]
        r = run_variant(symbol, name, params, label, bars=window, meta=meta,
                        evaluate_from=fold.test_start - fold.train_start)
        returns.extend(r.returns)
        trades += r.trades
    equity = [1.0]
    for r in returns:
        equity.append(equity[-1] * (1 + r))
    return sharpe_ratio(returns), (equity[-1] - 1) * 100, max_drawdown_pct(equity), trades


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="*", default=["GOLDM", "SILVERM"])
    parser.add_argument("--train", type=int, default=DEFAULT_TRAIN)
    parser.add_argument("--test", type=int, default=DEFAULT_TEST)
    parser.add_argument("--step", type=int, default=DEFAULT_STEP)
    args = parser.parse_args(argv)

    meta = load_meta()
    by_label = {lbl: (name, params) for lbl, name, params in BASE_GRID}

    all_deltas = []
    for symbol in args.symbols:
        bars = cache.load(symbol)
        folds = make_folds(len(bars), args.train, args.test, args.step)
        if not folds:
            print(f"{symbol}: too short for a fold", file=sys.stderr)
            continue

        cached: dict[str, tuple] = {}

        def scored(label):
            if label not in cached:
                name, params = by_label[label]
                cached[label] = oos_series(symbol, name, params, label, bars, folds, meta)
            return cached[label]

        print(f"=== {symbol}  ({len(folds)} folds, "
              f"{bars[folds[0].test_start].timestamp.date()} -> "
              f"{bars[folds[-1].test_end - 1].timestamp.date()})")
        print(f"{'pair':<26} {'baseline':>22} {'treatment':>22} {'dSharpe':>9}")
        print(f"{'':<26} {'SR   ret%   trds':>22} {'SR   ret%   trds':>22}")
        print("-" * 84)
        for pair_label, base_lbl, treat_lbl in PAIRS:
            b = scored(base_lbl)
            t = scored(treat_lbl)
            delta = t[0] - b[0]
            all_deltas.append((symbol, pair_label, delta, b, t))
            print(f"{pair_label:<26} "
                  f"{b[0]:>6.2f} {b[1]:>7.1f}% {b[3]:>5} "
                  f"{t[0]:>6.2f} {t[1]:>7.1f}% {t[3]:>5} "
                  f"{delta:>+9.2f}")
        print()

    print("=" * 84)
    better = [d for d in all_deltas if d[2] > 0.05]
    worse = [d for d in all_deltas if d[2] < -0.05]
    print(f"treatment better by >0.05 Sharpe: {len(better)} of {len(all_deltas)}")
    print(f"treatment worse   by >0.05 Sharpe: {len(worse)} of {len(all_deltas)}")
    if all_deltas:
        print(f"mean dSharpe across all pairs: "
              f"{sum(d[2] for d in all_deltas) / len(all_deltas):+.3f}")
    print("\nConsistent across BOTH instruments (the only kind worth acting on):")
    by_pair: dict[str, list] = {}
    for symbol, pair_label, delta, _, _ in all_deltas:
        by_pair.setdefault(pair_label, []).append(delta)
    for pair_label, deltas in by_pair.items():
        if len(deltas) > 1 and all(d > 0.05 for d in deltas):
            print(f"  BETTER on all: {pair_label:<26} {[f'{d:+.2f}' for d in deltas]}")
        elif len(deltas) > 1 and all(d < -0.05 for d in deltas):
            print(f"  WORSE  on all: {pair_label:<26} {[f'{d:+.2f}' for d in deltas]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
