"""Run six monthly option strategies across the 210-name F&O universe.

    python -m research.stock_options.run_strategies [--symbols ...] [--limit N]

**No database, ever.** Outputs land in `research/.output/stock_options/`.

PER-STOCK IS THE DELIVERABLE, ranked by CAGR and by Sharpe, with each stock's
own buy-and-hold beside it so a high rank that still lost to simply owning the
shares is visible at a glance. The pooled view sits underneath those tables,
not instead of them.

The leaderboard is also, structurally, the thing the F&O equity study warned
about: 210 stocks x 6 strategies ranked means the top is partly luck, and each
stock has only ~84 monthly cycles behind it. `rank_stability.py` answers
whether the ordering is real; this module just produces it honestly.

ADJUSTED PRICE SPACE. Option strikes are unadjusted -- they are the strikes
that actually traded -- while the cached cash series is corporate-action
adjusted. Holding delivered shares through a 1:1 bonus would otherwise show a
fake 50% equity collapse (HDFCBANK's factor jumps 0.25 -> 0.50 mid-sample).
Every price is therefore converted into adjusted space before the engine sees
it, which makes the series continuous. Absolute rupee amounts end up in a
rescaled unit; every RETURN is exact, because capital and P&L scale together.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

from growmore_bot.backtest.metrics import cagr_pct, max_drawdown_pct, sharpe_ratio
from research.fno import bar_cache as cash_bars
from research.fno.manifest import load_manifest
from research.stock_options import chain_cache
from research.stock_options.pricing import realised_vol
from research.stock_options.wheel_engine import STRATEGIES, WheelResult, run_wheel

OUTPUT_DIR = Path(__file__).resolve().parent.parent / ".output" / "stock_options"
INITIAL_CAPITAL = 1_000_000.0
REALISED_VOL_WINDOW = 20
#: Fewer monthly cycles than this and a stock is "unmeasured", not "weak" --
#: the treatment docs/walk-forward-results.md gave short-history contracts.
MIN_CYCLES = 12


def adjustment_factors(symbol: str, chain: pd.DataFrame) -> Optional[pd.Series]:
    """adjusted_close / unadjusted_close per trading day.

    Piecewise constant, jumping only on a corporate action -- which makes it
    both the conversion factor and a free corporate-action detector.
    """
    try:
        bars = cash_bars.load(symbol)
    except FileNotFoundError:
        return None
    adjusted = {cash_bars.trading_date(b.timestamp): float(b.close) for b in bars}
    unadjusted = chain.groupby("trade_date")["underlying"].first()
    rows = {}
    for day, spot in unadjusted.items():
        key = pd.Timestamp(day).date()
        if spot and spot > 0 and key in adjusted and adjusted[key] > 0:
            rows[pd.Timestamp(day)] = adjusted[key] / float(spot)
    return pd.Series(rows).sort_index() if rows else None


def to_adjusted_space(chain: pd.DataFrame, factors: pd.Series) -> pd.DataFrame:
    """Rescale strikes and premiums so the price series is continuous."""
    out = chain.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"])
    f = out["trade_date"].map(factors)
    out = out[f.notna()].copy()
    f = f[f.notna()]
    for column in ("strike", "settle", "close", "open", "high", "low", "underlying"):
        out[column] = out[column] * f
    return out


def buy_and_hold(symbol: str, first_day, last_day) -> Optional[dict]:
    """The mandatory control: own the stock over the identical window."""
    try:
        bars = cash_bars.load(symbol)
    except FileNotFoundError:
        return None
    closes = [
        float(b.close)
        for b in bars
        if first_day <= pd.Timestamp(cash_bars.trading_date(b.timestamp)) <= last_day
    ]
    if len(closes) < 30:
        return None
    equity = [INITIAL_CAPITAL * c / closes[0] for c in closes]
    rets = [(b / a - 1) for a, b in zip(equity, equity[1:]) if a != 0]
    years = max((last_day - first_day).days / 365.25, 0.0)
    return {
        "cagr_pct": cagr_pct(INITIAL_CAPITAL, equity[-1], years) if years else 0.0,
        "sharpe": sharpe_ratio(rets),
        "max_drawdown_pct": max_drawdown_pct(equity),
    }


def realised_vol_by_day(chain: pd.DataFrame) -> dict:
    """Trailing realised vol of the underlying, for strategy D."""
    spot = chain.groupby("trade_date")["underlying"].first().sort_index()
    values = list(spot.values)
    out = {}
    for i, day in enumerate(spot.index):
        window = values[max(0, i - REALISED_VOL_WINDOW) : i + 1]
        out[pd.Timestamp(day)] = realised_vol(window)
    return out


def run_symbol(
    symbol: str,
    from_date: Optional[pd.Timestamp] = None,
    to_date: Optional[pd.Timestamp] = None,
    min_cycles: int = MIN_CYCLES,
) -> tuple[list[WheelResult], Optional[dict]]:
    """Every strategy on one stock, optionally over a sub-window.

    The window is what `rank_stability` uses to ask whether a stock's rank in
    one period says anything about its rank in the next.
    """
    chain = chain_cache.load_symbol(symbol)
    if chain.empty:
        return [], None
    chain["trade_date"] = pd.to_datetime(chain["trade_date"])
    if from_date is not None:
        chain = chain[chain["trade_date"] >= from_date]
    if to_date is not None:
        chain = chain[chain["trade_date"] <= to_date]
    if chain.empty:
        return [], None
    factors = adjustment_factors(symbol, chain)
    if factors is None:
        return [], None
    chain = to_adjusted_space(chain, factors)
    if chain.empty:
        return [], None

    rv = realised_vol_by_day(chain)
    days = pd.to_datetime(chain["trade_date"])
    control = buy_and_hold(symbol, days.min(), days.max())

    results = []
    for config in STRATEGIES:
        try:
            result = run_wheel(
                symbol, chain, config,
                initial_capital=INITIAL_CAPITAL, realised_vol_by_day=rv,
            )
        except Exception as exc:  # noqa: BLE001 -- one stock must not lose the run
            print(f"  {symbol} {config.tag}: FAILED {str(exc)[:70]}", file=sys.stderr)
            continue
        if result is not None and result.cycles >= min_cycles:
            results.append(result)
    return results, control


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    manifest = {r.symbol: r for r in load_manifest()}
    available = chain_cache.cached_symbols()
    symbols = [s for s in available if s in manifest]
    if args.symbols:
        wanted = set(args.symbols)
        symbols = [s for s in symbols if s in wanted]
    if args.limit:
        symbols = symbols[: args.limit]
    print(f"{len(symbols)} symbols with cached chains", file=sys.stderr)

    rows: list[dict] = []
    unmeasured: list[str] = []
    for index, symbol in enumerate(symbols, start=1):
        results, control = run_symbol(symbol)
        if not results or control is None:
            unmeasured.append(symbol)
            continue
        info = manifest[symbol]
        for r in results:
            rows.append({
                "symbol": symbol,
                "sector": info.nse_industry,
                "is_defence": info.is_defence,
                "strategy": r.tag,
                "cycles": r.cycles,
                "cagr_pct": round(r.cagr_pct, 3),
                "sharpe": round(r.sharpe, 4),
                "max_drawdown_pct": round(r.max_drawdown_pct, 3),
                "time_underwater_pct": round(r.time_underwater_pct, 2),
                "time_frozen_pct": round(r.time_frozen_pct, 2),
                "assignment_rate_pct": round(r.assignment_rate_pct, 2),
                "win_rate_pct": round(r.win_rate_pct, 2),
                "total_cost": round(r.total_cost, 2),
                "final_equity": round(r.final_equity, 2),
                "bh_cagr_pct": round(control["cagr_pct"], 3),
                "bh_sharpe": round(control["sharpe"], 4),
                "bh_max_drawdown_pct": round(control["max_drawdown_pct"], 3),
            })
        if index % 20 == 0:
            print(f"  [{index}/{len(symbols)}] {symbol}", file=sys.stderr)

    if not rows:
        print("\nNothing measured -- fetch and consolidate the chains first.",
              file=sys.stderr)
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "per_stock.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n{len(rows)} (stock, strategy) results over "
          f"{len({r['symbol'] for r in rows})} stocks; "
          f"{len(unmeasured)} unmeasured", file=sys.stderr)
    _print_rankings(rows)
    print(f"\n-> {path}", file=sys.stderr)
    return 0


def _print_rankings(rows: list[dict], top: int = 20) -> None:
    header = (
        f"{'#':>3} {'symbol':<13}{'strategy':<28}{'CAGR':>8}{'B&H':>8}"
        f"{'Sharpe':>8}{'B&H':>7}{'MaxDD':>8}{'assign%':>8}"
    )
    for metric, label in (("cagr_pct", "CAGR"), ("sharpe", "Sharpe")):
        print(f"\n--- TOP {top} BY {label} (best-of-N: see rank_stability) ---")
        print(header)
        print("-" * len(header))
        for i, r in enumerate(sorted(rows, key=lambda x: -x[metric])[:top], 1):
            print(f"{i:>3} {r['symbol']:<13}{r['strategy']:<28}"
                  f"{r['cagr_pct']:>7.1f}%{r['bh_cagr_pct']:>7.1f}%"
                  f"{r['sharpe']:>8.2f}{r['bh_sharpe']:>7.2f}"
                  f"{r['max_drawdown_pct']:>7.1f}%{r['assignment_rate_pct']:>7.1f}%")

    print("\n--- BY STRATEGY (median across stocks) ---")
    print(f"{'strategy':<28}{'n':>5}{'medCAGR':>9}{'medSharpe':>11}{'medDD':>8}"
          f"{'beats B&H CAGR':>16}{'beats B&H Sharpe':>18}")
    import statistics as st
    for tag in sorted({r["strategy"] for r in rows}):
        g = [r for r in rows if r["strategy"] == tag]
        wc = sum(1 for r in g if r["cagr_pct"] > r["bh_cagr_pct"])
        ws = sum(1 for r in g if r["sharpe"] > r["bh_sharpe"])
        print(f"{tag:<28}{len(g):>5}{st.median(r['cagr_pct'] for r in g):>8.1f}%"
              f"{st.median(r['sharpe'] for r in g):>11.2f}"
              f"{st.median(r['max_drawdown_pct'] for r in g):>7.1f}%"
              f"{wc/len(g)*100:>15.1f}%{ws/len(g)*100:>17.1f}%")


if __name__ == "__main__":
    raise SystemExit(main())
