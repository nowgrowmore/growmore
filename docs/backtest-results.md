# Backtest Results — full sweep, 2026-09-05 (post-fix, with a benchmark)

**296 runs: 37 variants × 8 MCX commodities, 5 years of real Dhan daily data (2021-09-05 →
2026-09-05).** Per-instrument capital (one lot's own notional = 1x leverage), real MCX costs and
tick slippage charged per leg, price series duplicate-repaired to the higher-volume front-month bar.

Two things changed since the previous table, and both matter more than any individual number:

1. **The trailing-stop lookahead is fixed.** Every `risk_managed` figure published before this run
   was produced by code that ratcheted the stop from a bar's own high and then tested it against
   that same bar's low. See `docs/technical-debt.md`.
2. **There is a benchmark now.** `buy_and_hold` is a real strategy in the grid, run through the
   same engine and cost model. No ranking in this file appears without it.

> **Read `docs/walk-forward-results.md` before acting on anything here.** Everything below is
> *in-sample* — the variants were chosen and measured on the same five years. The bottom line of
> this page is that the best variant beats buy-and-hold on 8 of 8 instruments. The bottom line of
> the out-of-sample test is that it **loses on 5 of 8**. Both are true; only the second is evidence.
> The gap between them is what selecting the best of 37 variants per instrument is worth, which is
> to say: most of it.

## The benchmark

| Instrument | CAGR | Sharpe | Max DD |
|---|---|---|---|
| Gold Mini | 26.2% | 1.48 | 18.6% |
| Silver Mini | 29.7% | 0.99 | 47.1% |
| Aluminium Mini | 14.6% | 0.88 | 16.7% |
| Copper | 14.0% | 0.79 | 27.1% |
| Zinc Mini | 12.8% | 0.76 | 25.5% |
| Crude Oil Mini | 8.1% | 0.40 | 38.8% |
| Nickel | 2.0% | 0.23 | 67.4% |
| Lead Mini | 2.1% | 0.21 | 12.4% |

One lot, held for five years, rolled at expiry. Rolling costs ~2.4 bps a round trip — about 1.5%
over the whole period even at monthly rolls, which is inside the rounding on everything else here.

## Top 15 by Sharpe, with the margin over holding

| Strategy | Variant | Inst | Trades | CAGR | Sharpe | MaxDD | vs hold | DSR |
|---|---|---|---|---|---|---|---|---|
| risk_managed | boll20-2.5-notrail | ZINCMINI | **6** | 15.3% | 1.89 | 6.5% | +1.13 | 0.96 |
| **vol_filtered** | macd5-13-5-stop2-trail3-**vol90** | GOLDM | 72 | 19.7% | **1.69** | 12.4% | +0.21 | 0.88 |
| **vol_filtered** | ensemble-agree3-stop2-trail3-**vol90** | GOLDM | 39 | 20.1% | 1.68 | 12.5% | +0.20 | 0.89 |
| **vol_filtered** | ensemble-agree3-stop2-trail3-**vol90** | SILVERM | 43 | **40.6%** | 1.65 | 16.3% | **+0.66** | 0.87 |
| bollinger_reversion | period20-k2.5 | ZINCMINI | **5** | 14.2% | 1.63 | 11.8% | +0.88 | 0.86 |
| risk_managed | ensemble-agree3 | GOLDM | 47 | 20.6% | 1.62 | 12.3% | +0.14 | 0.85 |
| risk_managed | macd5-13-5 | GOLDM | 86 | 21.1% | 1.57 | 11.7% | +0.09 | 0.81 |
| risk_managed | macd12-26-9 | GOLDM | 45 | 20.5% | 1.55 | 10.8% | +0.07 | 0.81 |
| risk_managed | ensemble-agree3 | SILVERM | 49 | 39.3% | 1.52 | 16.3% | +0.54 | 0.80 |
| risk_managed | macd5-13-5 | SILVERM | 88 | 35.9% | 1.49 | 20.5% | +0.50 | 0.77 |
| macd_trend | fast5-slow13-sig5 *(incumbent)* | GOLDM | 86 | 22.0% | 1.43 | 19.1% | **−0.05** | 0.69 |
| risk_managed | macd12-26-9 | SILVERM | 42 | 36.3% | 1.41 | 17.5% | +0.42 | 0.72 |
| risk_managed | donchian20 | SILVERM | 26 | 36.9% | 1.35 | 21.0% | +0.36 | 0.66 |
| risk_managed | macd5-13-5-stop3-notrail | GOLDM | 86 | 21.0% | 1.32 | 19.8% | **−0.16** | 0.62 |
| regime_switch | adx14-macd5135-vwap_ema | ALUMINI | 38 | 16.2% | 1.29 | 7.4% | +0.41 | 0.61 |

Effective trials 18; the selection-luck bar is now **Sharpe 1.16** — the best of 18 independent
tries would be expected to reach that by chance alone. `buy_and_hold` is excluded from that count:
it is the benchmark, not something we searched for, and counting it would charge the strategies for
the privilege of being compared against holding.

## What to take from this

**The volatility filter is the real addition.** It takes the top three non-fluke slots and is the
only new idea in months that also survived out-of-sample testing on both bullion contracts. On
Silver Mini it posts **40.6% CAGR at 1.65 Sharpe with a 16.3% drawdown** against a benchmark of
29.7% / 0.99 / 47.1% — better on all three axes, and by a wide margin.

**Gold Mini and Silver Mini remain cleanly separated, exactly as out-of-sample said.** Gold Mini's
best variant beats holding on Sharpe (+0.21) but **earns 6.6 percentage points a year less**. Silver
Mini's beats it on Sharpe (+0.66) *and* earns **11.0 points a year more**. On Gold Mini the
machinery buys a smoother ride; on Silver Mini it buys money.

**The currently-enabled Gold Mini incumbent loses to holding.** `macd_trend fast5-slow13-sig5`
scores −0.05 against the benchmark. It is still enabled in paper.

**Nothing clears DSR 0.95 except a six-trade fluke.** `risk_managed boll20-2.5-notrail` on Zinc
Mini closes **6** trades in three and a half years — under this repo's own 15-trade guardrail,
which flags but never drops. Its neighbour on the list closes 5. Ignore both.

**The "best variant beat buy-and-hold on 8 of 8" line at the bottom of the report is not a
result.** Picking the highest of 37 variants per instrument will beat almost anything in-sample.
The same question asked out-of-sample gives 3 of 8.

## Reproducing

```bash
cd bot
python -m growmore_bot.backtest.run_all --from-date 2021-09-05 --to-date 2026-09-05
python -m growmore_bot.backtest.run_all --from-date 2021-09-05 --to-date 2026-09-05 --symbols GOLDM SILVERM
python -m research.validation.deflate_sweep --hours 6 --persist
python -m research.validation.sweep_report --hours 6
```

Runs commit per instrument, so an interrupted sweep leaves whole instruments done and none
half-done — `--symbols` finishes the rest without redoing them.
