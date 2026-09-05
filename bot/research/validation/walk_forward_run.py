"""Walk-forward validation: select on the past, score on the future.

    python -m research.validation.walk_forward_run --symbols GOLDM SILVERM

Every published number in this repo is in-sample. The sweep runs a grid over
one window and reports the best of it; `deflated_sharpe.py` then discounts
that number for the size of the search. Deflation is a correction, not
evidence. This asks the question deflation cannot: if you had picked a variant
using only data available at the time, would it have made money in the six
months that followed?

Three things are reported per instrument, and the comparison between them IS
the result:

  selected  -- re-pick the best training-window variant at every fold
  fixed     -- risk_managed macd12-26-9 always, never re-picked (the NULL)
  incumbent -- risk_managed macd5-13-5, what is running in paper today
  buy&hold  -- one lot held through every test window, same costs

Buy-and-hold is not a formality here. The out-of-sample span lands on
2023-11 to 2026-05, which is the strongest stretch of the precious-metals
bull run in the whole dataset -- so a high out-of-sample Sharpe is the DEFAULT
outcome, not a discovery. The only number that means anything is the margin
over holding the contract.

If `selected` does not beat `fixed`, the honest conclusion is "stop selecting,
run one thing" -- which is a useful finding, not a failed experiment.

DECLARED BEFORE RUNNING (recorded here so it cannot be quietly retuned):
  * geometry: train 504 / test 126 / step 126, rolling (see walk_forward.py)
  * selection metric: SHARPE, among training runs with >= 8 closed trades.
    Not profit factor, which run_all.py ranks by -- PF on a handful of trades
    is what put a six-trade ZINCMINI run at the top of the sweep.
  * headline number: the stitched out-of-sample Sharpe per instrument.
"""
from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass
from typing import Any, Sequence

from growmore_bot.backtest.metrics import max_drawdown_pct, sharpe_ratio
from growmore_bot.backtest.walk_forward import (
    DEFAULT_STEP,
    DEFAULT_TEST,
    DEFAULT_TRAIN,
    Fold,
    grid_hash,
    make_folds,
)
from research.dailydata import cache
from research.dailydata.fetch import load_meta
from research.dailydata.runner import risk_managed, run_variant

MIN_TRAIN_TRADES = 8

MACD_FAST = {"fast_period": 5, "slow_period": 13, "signal_period": 5}
MACD_MED = {"fast_period": 8, "slow_period": 21, "signal_period": 7}
MACD_SLOW = {"fast_period": 12, "slow_period": 26, "signal_period": 9}

#: The frozen selection grid: (label, strategy_name, params).
#: This is the SAME grid the in-sample sweep searched, so the walk-forward
#: result speaks to the sweep's own conclusions rather than to a new search.
BASE_GRID: list[tuple[str, str, dict]] = [
    ("rm-macd5-13-5", "risk_managed", risk_managed("macd_trend", MACD_FAST, 2.0, 3.0)),
    ("rm-macd8-21-7", "risk_managed", risk_managed("macd_trend", MACD_MED, 2.0, 3.0)),
    ("rm-macd12-26-9", "risk_managed", risk_managed("macd_trend", MACD_SLOW, 2.0, 3.0)),
    ("rm-macd5-13-5-stop3-notrail", "risk_managed",
     risk_managed("macd_trend", MACD_FAST, 3.0, None)),
    ("rm-ensemble-agree3", "risk_managed",
     risk_managed("ensemble_trend", {"min_agreement": 3}, 2.0, 3.0)),
    ("rm-ensemble-agree4", "risk_managed",
     risk_managed("ensemble_trend", {"min_agreement": 4}, 2.0, 3.0)),
    ("rm-donchian20", "risk_managed", risk_managed("donchian_breakout", {"period": 20}, 2.0, 3.0)),
    ("rm-donchian55", "risk_managed", risk_managed("donchian_breakout", {"period": 55}, 2.0, 3.0)),
    ("rm-sma5-20", "risk_managed",
     risk_managed("sma_crossover", {"fast_period": 5, "slow_period": 20}, 2.0, 3.0)),
    ("rm-sma10-30", "risk_managed",
     risk_managed("sma_crossover", {"fast_period": 10, "slow_period": 30}, 2.0, 3.0)),
    ("rm-rsi7", "risk_managed",
     risk_managed("rsi_mean_reversion", {"period": 7, "oversold": 30, "overbought": 70}, 2.0, None)),
    ("rm-rsi14", "risk_managed",
     risk_managed("rsi_mean_reversion", {"period": 14, "oversold": 30, "overbought": 70}, 2.0, None)),
    ("rm-boll20-2.5", "risk_managed",
     risk_managed("bollinger_reversion", {"period": 20, "num_std": 2.5}, 2.0, None)),
    # --- Phase 4 additions. Each is measured ONLY here, never added to the
    # in-sample sweep, so they cannot inflate the effective-trials count that
    # the published DSR figures are discounted by.
    # 4a slow trend: the untested region of the spectrum (arXiv 2504.10914).
    ("ema80", "ema_trend", {"period": 80}),
    ("ema112", "ema_trend", {"period": 112}),
    ("ema150", "ema_trend", {"period": 150}),
    ("rm-ema112", "risk_managed", risk_managed("ema_trend", {"period": 112}, 2.0, 3.0)),
    # 4b no-trade buffer: theta=0 is the existing rm-macd5-13-5 exactly.
    ("rm-macd5-13-5-buf0.1", "risk_managed",
     risk_managed("macd_trend", dict(MACD_FAST, buffer_atr=0.1), 2.0, 3.0)),
    ("rm-macd5-13-5-buf0.25", "risk_managed",
     risk_managed("macd_trend", dict(MACD_FAST, buffer_atr=0.25), 2.0, 3.0)),
    ("rm-macd12-26-9-buf0.25", "risk_managed",
     risk_managed("macd_trend", dict(MACD_SLOW, buffer_atr=0.25), 2.0, 3.0)),
    # 4c volatility admission: RealizedVolCalculator had never been used.
    ("vol90-rm-macd5-13-5", "vol_filtered", {
        "inner_strategy": "risk_managed",
        "inner_params": risk_managed("macd_trend", MACD_FAST, 2.0, 3.0),
        "vol_window": 20, "lookback": 504, "percentile_cap": 0.90}),
    ("vol80-rm-macd5-13-5", "vol_filtered", {
        "inner_strategy": "risk_managed",
        "inner_params": risk_managed("macd_trend", MACD_FAST, 2.0, 3.0),
        "vol_window": 20, "lookback": 504, "percentile_cap": 0.80}),
    ("vol90-rm-ensemble", "vol_filtered", {
        "inner_strategy": "risk_managed",
        "inner_params": risk_managed("ensemble_trend", {"min_agreement": 3}, 2.0, 3.0),
        "vol_window": 20, "lookback": 504, "percentile_cap": 0.90}),
    # 4d the time stop: implemented and tested since Phase 3 of the risk
    # layer, and never once set by any sweep -- dead code until now.
    ("rm-macd5-13-5-time30", "risk_managed",
     risk_managed("macd_trend", MACD_FAST, 2.0, 3.0, max_bars=30)),
    ("rm-macd5-13-5-time60", "risk_managed",
     risk_managed("macd_trend", MACD_FAST, 2.0, 3.0, max_bars=60)),
    ("rm-ensemble-time60", "risk_managed",
     risk_managed("ensemble_trend", {"min_agreement": 3}, 2.0, 3.0, max_bars=60)),
    # 4e stop calibration: every variant above uses 2.0/3.0, implicitly tuned
    # on gold. Silver's ATR is proportionally much larger.
    ("rm-macd5-13-5-stop1.5-trail2", "risk_managed",
     risk_managed("macd_trend", MACD_FAST, 1.5, 2.0)),
    ("rm-macd5-13-5-stop3-trail4", "risk_managed",
     risk_managed("macd_trend", MACD_FAST, 3.0, 4.0)),
    ("rm-ensemble-stop1.5-trail2", "risk_managed",
     risk_managed("ensemble_trend", {"min_agreement": 3}, 1.5, 2.0)),
    ("rm-ensemble-stop3-trail4", "risk_managed",
     risk_managed("ensemble_trend", {"min_agreement": 3}, 3.0, 4.0)),

    ("macd5-13-5", "macd_trend", MACD_FAST),
    ("macd12-26-9", "macd_trend", MACD_SLOW),
    ("ensemble-agree3", "ensemble_trend", {"min_agreement": 3}),
    ("donchian20", "donchian_breakout", {"period": 20}),
    ("sma10-30", "sma_crossover", {"fast_period": 10, "slow_period": 30}),
]

NULL_VARIANT = ("rm-macd12-26-9", "risk_managed", risk_managed("macd_trend", MACD_SLOW, 2.0, 3.0))
INCUMBENT = ("rm-macd5-13-5", "risk_managed", risk_managed("macd_trend", MACD_FAST, 2.0, 3.0))


@dataclass
class FoldOutcome:
    fold: Fold
    chosen: str
    train_sharpe: float
    test_sharpe: float
    test_return_pct: float
    test_trades: int
    returns: list[float]


def _score(symbol, bars, name, params, label, fold, meta, evaluate_from):
    return run_variant(
        symbol, name, params, label,
        bars=bars, meta=meta, evaluate_from=evaluate_from,
    )


def select_for_fold(
    symbol: str, bars: Sequence[Any], fold: Fold, grid, meta: dict
) -> tuple[str, str, dict, float]:
    """Pick the best variant using ONLY [train_start, train_end)."""
    train_bars = bars[fold.train_start : fold.train_end]
    best = None
    for label, name, params in grid:
        r = _score(symbol, train_bars, name, params, label, fold, meta, 0)
        if r.trades < MIN_TRAIN_TRADES:
            continue
        if best is None or r.sharpe > best[3]:
            best = (label, name, params, r.sharpe)
    if best is None:
        # Nothing traded enough to judge -- fall back to the declared null
        # rather than to whatever happened to trade twice.
        label, name, params = NULL_VARIANT
        return label, name, params, float("nan")
    return best


def run_instrument(
    symbol: str, meta: dict, grid, train: int, test: int, step: int
) -> dict:
    bars = cache.load(symbol)
    folds = make_folds(len(bars), train=train, test=test, step=step)
    if not folds:
        return {"symbol": symbol, "folds": [], "n_bars": len(bars)}

    outcomes: list[FoldOutcome] = []
    fixed_returns: list[float] = []
    incumbent_returns: list[float] = []
    selected_returns: list[float] = []
    hold_returns: list[float] = []

    for fold in folds:
        label, name, params, train_sharpe = select_for_fold(symbol, bars, fold, grid, meta)

        # Warm up over the training window, score only the test window. The
        # strategy sees the training bars (they are its own past); the
        # SELECTION never saw the test bars, which is the property that matters.
        window = bars[fold.train_start : fold.test_end]
        offset = fold.test_start - fold.train_start

        chosen = _score(symbol, window, name, params, label, fold, meta, offset)
        selected_returns.extend(chosen.returns)

        fixed = _score(symbol, window, NULL_VARIANT[1], NULL_VARIANT[2],
                       NULL_VARIANT[0], fold, meta, offset)
        fixed_returns.extend(fixed.returns)

        inc = _score(symbol, window, INCUMBENT[1], INCUMBENT[2],
                     INCUMBENT[0], fold, meta, offset)
        incumbent_returns.extend(inc.returns)

        # Buy-and-hold over the same test bars: close-to-close, unlevered, so
        # it is directly comparable to the strategies' equity returns.
        test_bars = bars[fold.test_start : fold.test_end]
        hold_returns.extend(
            (float(b.close) / float(a.close) - 1)
            for a, b in zip(test_bars, test_bars[1:])
            if float(a.close)
        )

        outcomes.append(FoldOutcome(
            fold=fold, chosen=label, train_sharpe=train_sharpe,
            test_sharpe=chosen.sharpe,
            test_return_pct=(chosen.final_equity / chosen.initial_capital - 1) * 100
            if chosen.initial_capital else 0.0,
            test_trades=chosen.trades, returns=chosen.returns,
        ))

    return {
        "symbol": symbol,
        "n_bars": len(bars),
        "folds": outcomes,
        "selected": selected_returns,
        "fixed": fixed_returns,
        "incumbent": incumbent_returns,
        "buy_hold": hold_returns,
        "first_date": bars[folds[0].test_start].timestamp.date(),
        "last_date": bars[folds[-1].test_end - 1].timestamp.date(),
    }


def _stitched(returns: list[float]) -> tuple[float, float, float]:
    """(annualised Sharpe, total return %, max drawdown %) of a stitched curve."""
    if not returns:
        return 0.0, 0.0, 0.0
    equity = [1.0]
    for r in returns:
        equity.append(equity[-1] * (1 + r))
    return sharpe_ratio(returns), (equity[-1] - 1) * 100, max_drawdown_pct(equity)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="*", default=None,
                        help="Default: every symbol with a daily cache.")
    parser.add_argument("--train", type=int, default=DEFAULT_TRAIN)
    parser.add_argument("--test", type=int, default=DEFAULT_TEST)
    parser.add_argument("--step", type=int, default=DEFAULT_STEP)
    args = parser.parse_args(argv)

    meta = load_meta()
    symbols = args.symbols or cache.cached_symbols()
    grid = BASE_GRID
    ghash = grid_hash([(lbl, params) for lbl, _, params in grid])

    print(f"grid: {len(grid)} variants, hash {ghash}")
    print(f"geometry: train {args.train} / test {args.test} / step {args.step} (rolling)")
    print(f"selection metric: Sharpe, min {MIN_TRAIN_TRADES} training trades")
    print()

    summary = []
    for symbol in symbols:
        res = run_instrument(symbol, meta, grid, args.train, args.test, args.step)
        if not res["folds"]:
            print(f"{symbol}: only {res['n_bars']} bars -- not enough for one fold\n")
            continue

        print(f"=== {symbol}  ({res['n_bars']} bars, OOS {res['first_date']} -> {res['last_date']})")
        print(f"{'fold':>4} {'train window':>14} {'chosen':<28} {'trainSR':>8} "
              f"{'testSR':>7} {'test%':>8} {'trds':>5}")
        for o in res["folds"]:
            ts = "  n/a" if o.train_sharpe != o.train_sharpe else f"{o.train_sharpe:8.2f}"
            print(f"{o.fold.index:>4} {o.fold.train_start:>6}-{o.fold.train_end:<7} "
                  f"{o.chosen:<28} {ts} {o.test_sharpe:>7.2f} "
                  f"{o.test_return_pct:>7.1f}% {o.test_trades:>5}")

        row = {"symbol": symbol}
        for key in ("selected", "fixed", "incumbent", "buy_hold"):
            sr, tot, dd = _stitched(res[key])
            row[key] = (sr, tot, dd)
        summary.append(row)

        chosen_labels = [o.chosen for o in res["folds"]]
        print(f"     distinct variants chosen across folds: {len(set(chosen_labels))}"
              f" of {len(chosen_labels)}")
        print()

    print("=" * 78)
    print("STITCHED OUT-OF-SAMPLE (all folds concatenated)")
    print(f"{'inst':<10} {'':<12} {'Sharpe':>7} {'total':>9} {'maxDD':>8}")
    print("-" * 50)
    for row in summary:
        for key, pretty in (("selected", "re-selected"), ("fixed", "fixed null"),
                            ("incumbent", "incumbent"), ("buy_hold", "buy & hold")):
            sr, tot, dd = row[key]
            print(f"{row['symbol'] if key == 'selected' else '':<10} {pretty:<12} "
                  f"{sr:>7.2f} {tot:>8.1f}% {dd:>7.1f}%")
        print("-" * 50)

    beats = [r for r in summary if r["selected"][0] > r["fixed"][0]]
    over_hold = [r for r in summary if r["fixed"][0] > r["buy_hold"][0]]
    print(f"\nre-selecting beat the fixed null on {len(beats)} of {len(summary)} instruments")
    print(f"the fixed null beat buy-and-hold on {len(over_hold)} of {len(summary)}")
    if summary:
        for key, pretty in (("selected", "selected"), ("fixed", "fixed"),
                            ("incumbent", "incumbent"), ("buy_hold", "buy&hold")):
            print(f"  mean OOS Sharpe {pretty:<10} "
                  f"{statistics.mean(r[key][0] for r in summary):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
