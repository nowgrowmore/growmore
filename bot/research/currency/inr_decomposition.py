"""How much of Gold Mini / Silver Mini's return is metal, and how much is rupee?

    python -m research.currency.inr_decomposition

MCX bullion = international price x USDINR x (1 + duty). Over 2021-09 to
2026-09 the rupee went ~74.35 -> ~95.38, so a long MCX position collected
roughly 28% of currency tailwind on top of whatever the metal did. Every
published CAGR in this repo includes that, undifferentiated, and no strategy
decision has ever been made knowing the split.

Method: divide the whole OHLC by that day's USD/INR to get a USD-denominated
series, then run the identical strategies on both with identical costs.

  * The duty is a slow-moving multiplicative LEVEL, not a return driver, so it
    cancels out of a returns comparison -- with one real exception noted in
    the output: duty moved 15% -> 6% (Jul 2024) -> 15%, and each step is a
    one-day jump in the INR series that does not exist in the USD one.
  * Costs are charged in the currency the series is quoted in, so the USD run
    understates the rupee cost of trading very slightly. At 2-5 bps a round
    trip on a daily book this is far below the effect being measured.

Reading the result: if Sharpe holds up on the USD series, the trend signal is
real and the rupee is a bonus. If it collapses, a large share of the headline
is currency drift -- which does not make the returns fake (the INR contract is
what you actually trade) but does mean the strategy is not what we think it
is, and any conclusion about the SIGNAL rests on a misreading.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Any, Sequence

from growmore_bot.backtest.metrics import max_drawdown_pct, sharpe_ratio
from research.currency import fx
from research.dailydata import cache
from research.dailydata.fetch import load_meta
from research.dailydata.runner import risk_managed, run_variant

MACD_FAST = {"fast_period": 5, "slow_period": 13, "signal_period": 5}
MACD_SLOW = {"fast_period": 12, "slow_period": 26, "signal_period": 9}

VARIANTS = [
    ("rm ensemble-agree3", "risk_managed",
     risk_managed("ensemble_trend", {"min_agreement": 3}, 2.0, 3.0)),
    ("rm macd5-13-5", "risk_managed", risk_managed("macd_trend", MACD_FAST, 2.0, 3.0)),
    ("rm macd12-26-9", "risk_managed", risk_managed("macd_trend", MACD_SLOW, 2.0, 3.0)),
    ("macd5-13-5 (bare)", "macd_trend", MACD_FAST),
    ("ensemble-agree3 (bare)", "ensemble_trend", {"min_agreement": 3}),
]

#: Import-duty steps inside the window. Each is a one-day jump in the INR
#: series with no counterpart in the USD one -- a genuine confound, called out
#: rather than smoothed away.
DUTY_STEPS = [
    (date(2022, 7, 1), "7.5% -> 12.5% basic customs duty"),
    (date(2024, 7, 23), "15% -> 6% total (Budget 2024)"),
    (date(2025, 2, 1), "back toward 15% via cess changes"),
]


def to_usd_series(bars: Sequence[Any], rates: dict) -> tuple[list, int]:
    """Re-denominate an OHLC series in USD. Returns (bars, dropped_count)."""
    out = []
    dropped = 0
    for b in bars:
        rate = rates.get(b.timestamp.date())
        if not rate:
            dropped += 1
            continue
        out.append(
            cache.CachedBar(
                timestamp=b.timestamp,
                open=b.open / rate, high=b.high / rate,
                low=b.low / rate, close=b.close / rate,
                volume=b.volume,
            )
        )
    return out, dropped


def buy_and_hold(bars: Sequence[Any]) -> tuple[float, float, float]:
    closes = [float(b.close) for b in bars if float(b.close)]
    rets = [b / a - 1 for a, b in zip(closes, closes[1:])]
    equity = [1.0]
    for r in rets:
        equity.append(equity[-1] * (1 + r))
    return sharpe_ratio(rets), (equity[-1] - 1) * 100, max_drawdown_pct(equity)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="*", default=["GOLDM", "SILVERM"])
    parser.add_argument("--force-fx", action="store_true")
    args = parser.parse_args(argv)

    meta = load_meta()

    for symbol in args.symbols:
        inr_bars = cache.load(symbol)
        days = [b.timestamp.date() for b in inr_bars]
        rates = fx.load(days, force=args.force_fx)
        usd_bars, dropped = to_usd_series(inr_bars, rates)

        span = f"{days[0]} -> {days[-1]}"
        window_rates = {d: r for d, r in rates.items() if days[0] <= d <= days[-1]}
        depr = fx.annualised_depreciation_pct(window_rates)
        first_rate = window_rates[min(window_rates)]
        last_rate = window_rates[max(window_rates)]

        print(f"=== {symbol}   {span}   {len(inr_bars)} bars"
              f"{f' ({dropped} dropped: no FX)' if dropped else ''}")
        print(f"    USDINR {first_rate:.2f} -> {last_rate:.2f} "
              f"= {(last_rate / first_rate - 1) * 100:+.1f}% total, "
              f"{depr:+.2f}%/yr of pure tailwind on a long position")

        # Meta for the USD run: the lot economics are unchanged in real terms,
        # but the capital rule keys off the first close, which is now in USD --
        # capital_for_run handles that consistently, so leverage stays 1x.
        usd_meta = {symbol: dict(meta[symbol])}
        # Ticks are quoted in rupees; in USD a tick is ~1/85th the size.
        usd_meta[symbol]["tick_size"] = float(meta[symbol]["tick_size"]) / last_rate

        sr_i, tot_i, dd_i = buy_and_hold(inr_bars)
        sr_u, tot_u, dd_u = buy_and_hold(usd_bars)
        print()
        print(f"    {'variant':<26} {'--- INR (traded) ---':>26} {'--- USD (metal only) ---':>28}"
              f" {'dSharpe':>8}")
        print(f"    {'':<26} {'CAGR':>8} {'Sharpe':>7} {'MaxDD':>9}"
              f" {'CAGR':>9} {'Sharpe':>7} {'MaxDD':>9} {'':>8}")
        print("    " + "-" * 92)
        print(f"    {'buy & hold':<26} {tot_i:>7.1f}% {sr_i:>7.2f} {dd_i:>8.1f}%"
              f" {tot_u:>8.1f}% {sr_u:>7.2f} {dd_u:>8.1f}% {sr_u - sr_i:>+8.2f}"
              f"   (total return, not CAGR)")

        for label, name, params in VARIANTS:
            r_inr = run_variant(symbol, name, params, label, bars=inr_bars, meta=meta)
            r_usd = run_variant(symbol, name, params, label, bars=usd_bars, meta=usd_meta)
            print(f"    {label:<26} {r_inr.cagr_pct:>7.1f}% {r_inr.sharpe:>7.2f}"
                  f" {r_inr.max_drawdown_pct:>8.1f}%"
                  f" {r_usd.cagr_pct:>8.1f}% {r_usd.sharpe:>7.2f}"
                  f" {r_usd.max_drawdown_pct:>8.1f}% {r_usd.sharpe - r_inr.sharpe:>+8.2f}")
        print()

    print("Import-duty steps inside the window (present in the INR series, absent in USD):")
    for when, what in DUTY_STEPS:
        print(f"  {when}  {what}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
