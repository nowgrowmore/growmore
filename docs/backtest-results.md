# Backtest Results — Strategy/Parameter Sweep (corrected 2026-09-04)

> **⚠️ The CAGR column below ranks LEVERAGE as much as edge — read this before acting on it
> (found 2026-09-05).** The engine trades exactly 1 lot per signal against one flat ₹5,00,000 of
> capital for every instrument, but MCX lot notionals span 44x: Crude Oil Mini is ₹0.78 lakh a lot
> while Copper is ₹34.3 lakh. So this sweep silently ran **0.16x leverage on Crude Oil Mini and
> 6.86x on Copper**. Dividing each result by its own leverage (run
> `python -m research.capital.admission --from-db` for the current table) re-states the published
> top five as return per unit of notional:
>
> | Published | Headline CAGR | Leverage | Per unit notional | Sharpe |
> | --- | --- | --- | --- | --- |
> | #1 MACD(5,13,5) / GOLDM | 21.9% | 3.06x | 7.2% | 1.38 |
> | #2 Regime-Switch / COPPER | 27.1% | 6.86x | **4.0%** | 1.17 |
> | #3 MACD(5,13,5) / SILVERM | 24.4% | 2.39x | 10.2% | 1.17 |
> | #4 MACD(12,26,9) / SILVERM | 25.4% | 2.39x | **10.6%** | 1.15 |
> | #5 MACD(12,26,9) / COPPER | 33.0% | 6.86x | **4.8%** | 0.99 |
> | *(not in the top 5)* MACD(12,26,9) / ALUMINI | 6.8% | 0.70x | **9.7%** | 1.06 |
>
> So **#5's headline 33.0% — the highest raw growth in the table — is the WORST of the five on an
> equal-risk basis**, and Aluminium Mini, dismissed below as "a notional-size effect", beats three
> of the top five. Note that `sharpe_ratio` was the honest column all along: Sharpe is
> leverage-invariant, CAGR is not, and the composite ranking mixed the two. Two results the
> leverage-free view surfaces that this table buries entirely: `regime_switch
> (adx14-macd5135-vwap_ema) / ALUMINI` at 13.6% per unit notional with **Sharpe 1.61 and a 3.4% max
> drawdown** over 32 trades — the best risk-adjusted result anywhere in the sweep, and a direct
> counterexample to `docs/goldmini-regime-switch-results.md`'s verdict, which tested Gold Mini only;
> and RSI(7,30/70) / NICKEL at 22.9% per unit notional, though on a poor Sharpe of 0.51.
>
> Dividing CAGR by leverage is a first-order correction, not a substitute for the real fix: re-run
> the sweep with `initial_capital` set per instrument to its own lot notional. Treat every CAGR
> below as provisional until that lands. The multiple-comparisons caveat at the bottom of this doc
> applies to the re-stated numbers exactly as it did to the originals.


> **Re-run 2026-09-04 after fixing the bugs described in `docs/technical-debt.md`** — most
> significantly, `run_all.py` sharing one stateful strategy instance across every instrument in a
> sweep (corrupting every commodity after the first one processed in any prior run), plus the frozen
> live-indicator bug and GOLDM's 10x lot_size error. The numbers below replace the previous
> (materially overstated) table — the old `backtest_runs` rows were deleted from the real Neon
> database and this sweep re-run fresh against it, not patched in place.

Full sweep: 6 strategy families (5 original + `regime_switch`) × parameter variants × 8 MCX
commodities = **144 backtest runs**, against 5 years of real Dhan historical daily data (2021-09 to
2026-09, per instrument's actual listing history). Persisted to the real Neon database — viewable in
full on the dashboard's [Backtests page](../dashboard/app/backtests/page.tsx) and
[Rankings page](../dashboard/app/rankings/page.tsx), which also explain every metric and every
strategy's parameters.

**Do not enable anything for paper trading off this alone.** See the caveats at the bottom before
acting on any of it.

## Top 5, highest to lowest

Ranked by a composite of CAGR (actual growth) and Sharpe (risk-adjusted quality), among results that
passed both guardrails below (>=15 closed trades, max drawdown <=50%).

| Rank | Strategy                             | Instrument  | Trades | CAGR      | Sharpe   | Profit factor | Max DD | Win rate  |
| ---- | ------------------------------------ | ----------- | ------ | --------- | -------- | ------------- | ------ | --------- |
| 1    | MACD Trend (5, 13, 5)                | Gold Mini   | 91     | 21.9%     | **1.38** | 3.20          | 24.4%  | 53.8%     |
| 2    | Regime-Switch (ADX+MACD 12,26,9+RSI) | Copper      | 19     | 27.1%     | 1.17     | **9.64**      | 23.9%  | **84.2%** |
| 3    | MACD Trend (5, 13, 5)                | Silver Mini | 88     | 24.4%     | 1.17     | 2.67          | 34.5%  | 51.1%     |
| 4    | MACD Trend (12, 26, 9)               | Silver Mini | 43     | 25.4%     | 1.15     | 6.15          | 33.8%  | 46.5%     |
| 5    | MACD Trend (12, 26, 9)               | Copper      | 50     | **33.0%** | 0.99     | 3.11          | 33.6%  | 52.0%     |

**#1 — MACD Trend (5,13,5) / Gold Mini**: the best combined growth+quality result, and by far the
largest sample (91 trades) of the top 5 — the least likely to be a fluke. Previously misreported as
78.2% CAGR / Sharpe 1.56 (see the correction note above); still the strongest all-around pick, just a
third of the size it looked like.

**#2 — Regime-Switch (ADX-gated MACD/RSI) / Copper**: the standout on quality (84% win rate, 9.64
profit factor) but only 19 trades — barely past the 15-trade guardrail. Worth watching, not yet
worth trusting the way #1 is.

**#3 / #4 — MACD Trend / Silver Mini (two parameter variants)**: both metal instruments, both MACD —
the strategy-family-level signal (see below) shows up again here.

**#5 — MACD Trend (12,26,9) / Copper**: the highest raw CAGR of the top 5, on a solid 50-trade sample,
but the lowest Sharpe of the group (0.99) — real growth, choppier ride.

**The strategy-family headline, still true after the correction**: MACD Trend appears **4 of the top
5** times, across 3 different commodities (Gold, Silver, Copper). That cross-commodity consistency for
one strategy family remains a stronger signal than any single number.

## Currently-configured strategies (for reference)

| Config (mode)                  | Strategy                        | Trades | CAGR  | Sharpe | Profit factor | Max DD | Win rate |
| ------------------------------ | -------------------------------- | ------ | ----- | ------ | -------------- | ------ | -------- |
| GOLDM `rsi_mean_reversion` (live) | RSI (period=7, 30/70)         | 39     | 14.6% | 1.10   | 3.46           | 22.1%  | 76.9%    |
| ALUMINI `macd_trend` (live)       | MACD (12, 26, 9)               | 33     | 6.8%  | 1.06   | 3.97           | 5.6%   | 63.6%    |
| GOLDM `macd_trend` (paper)        | MACD (5, 13, 5)                | 91     | 21.9% | 1.38   | 3.20           | 24.4%  | 53.8%    |
| COPPER `macd_trend` (paper)       | MACD (12, 26, 9)               | 50     | 33.0% | 0.99   | 3.11           | 33.6%  | 52.0%    |
| COPPER `macd_trend` (paper)       | MACD (5, 13, 5)                | 93     | 30.5% | 0.92   | 1.97           | 27.7%  | 46.2%    |
| SILVERM `macd_trend` (paper)      | MACD (12, 26, 9)               | 43     | 25.4% | 1.15   | 6.15           | 33.8%  | 46.5%    |

**GOLDM `rsi_mean_reversion` (the live config)** was previously reported at 60.8% CAGR — the real
number is 14.6%. Still a genuinely solid strategy (highest win rate of anything in this table, 3.46
profit factor, the smallest of the live/paper set's drawdowns relative to its return), just no longer
the standout that originally justified enabling it over every other option. Worth a fresh look before
treating it as "the proven one," not an urgent reason to disable it — nothing here suggests it's
losing money, only that the original growth number was inflated.

**ALUMINI `macd_trend` (the other live config)** barely moved (was ~7.6%, now 6.8%) — it was never
exposed to the worst of the bugs (small notional, not Gold-scale), so this one's history was
consistently reported both before and after.

## What *not* to conclude from this

- **112 of these 144 runs replace numbers that were flat-out wrong** (see the correction note at the
  top) — don't compare anything here against a memory of the old table, compare only within this one.
- **Aluminium Mini's small CAGR is a notional-size effect, not a weak edge** — its contract is much
  smaller notional value than Gold/Silver/Copper's, and the backtest engine trades exactly 1 lot per
  signal regardless of capital, so a clean, high-Sharpe edge there doesn't compound into much absolute
  growth. See the dashboard for its own strong risk-adjusted numbers.
- **"1 lot regardless of instrument" is not a margin-aware, capital-normalized position-sizing rule.**
  A fair CAGR comparison across commodities with very different lot notional values would need each
  instrument sized to use a consistent amount of capital/margin — not built yet.
- **The top 5 concentrate in 3 commodities (Gold, Silver, Copper) and one strategy family (MACD).**
  Picking several of these together does not diversify risk.
- **144 combinations were tested; the "top 5" is inherently subject to the multiple-comparisons
  trap.** No train/test split or walk-forward validation has been run yet — see
  `docs/pending-actions.md`.
- **`regime_switch`'s standout Copper result runs on only 19 trades.** Interesting, not yet
  trustworthy — see `docs/goldmini-regime-switch-results.md` for the earlier (Gold Mini-specific,
  also negative) verdict on this strategy family before reading too much into one thin Copper result.

## Methodology

- Ranking excludes any result with fewer than 15 closed trades, or with max drawdown over 50% — both
  flagged (not silently dropped) on the dashboard and in `bot/growmore_bot/backtest/run_all.py`.
- `profit_factor = None` in the database means "infinite" (zero losing trades in the sample), not
  zero.
- A fresh strategy instance is built per (instrument, variant) in the sweep — the bug that shared one
  instance across instruments is fixed; see `docs/technical-debt.md`.
- Full parameter grid, all 6 strategy definitions, and the ranking/flagging logic live in
  `bot/growmore_bot/backtest/run_all.py` and `bot/growmore_bot/strategies/`.
- Re-run: `python -m growmore_bot.backtest.run_all --from-date 2021-09-04 --to-date 2026-09-04`
  (from `bot/`, with `DATABASE_URL`/`DHAN_*` env vars set — see `bot/README.md`). This wraps the whole
  sweep in one long-lived DB transaction; over Neon's pooled connection this occasionally drops
  mid-run on a network blip (seen once during this re-run) — if it errors with "server closed the
  connection unexpectedly", nothing was committed (the transaction rolls back atomically), so just
  re-run the same command.

See `docs/pending-actions.md` for what's still needed before any of this influences real paper
trading (reviewing/enabling via the dashboard's Strategy Config page, setting real risk limits).
