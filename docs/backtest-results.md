# Backtest Results — Strategy/Parameter Sweep (2026-09-03)

Full sweep: 5 strategy families × 14 parameter variants × 8 MCX commodities = **112 backtest runs**,
against 5 years of real Dhan historical daily data (2021-09 to 2026-09, per instrument's actual
listing history). Persisted to the real Neon database — viewable in full on the dashboard's
[Backtests page](../dashboard/app/backtests/page.tsx), which also explains every metric and every
strategy's parameters.

**Do not enable anything for paper trading off this alone.** See the caveats at the bottom before
acting on any of it — this is a first, honest cut, not a validated recommendation.

## Top 5, highest to lowest

Ranked by a composite of CAGR (actual growth) and Sharpe (risk-adjusted quality), among results that
passed both filters below. Profit factor and win rate are also scale-invariant and worth weighing
directly.

| Rank | Strategy | Instrument | Trades | CAGR | Sharpe | Profit factor | Max DD | Win rate |
|---|---|---|---|---|---|---|---|---|
| 1 | MACD Trend (5, 13, 5) | Gold Mini | 91 | **78.2%** | **1.56** | 3.20 | 37.4% | 53.8% |
| 2 | RSI Mean-Reversion (period=7, 30/70) | Gold Mini | 39 | 60.8% | 1.22 | 3.46 | 39.6% | **76.9%** |
| 3 | MACD Trend (12, 26, 9) | Copper | 48 | 35.1% | 1.06 | 3.26 | 26.7% | 54.2% |
| 4 | MACD Trend (5, 13, 5) | Copper | 91 | 32.5% | 0.98 | 2.16 | 24.9% | 48.4% |
| 5 | MACD Trend (12, 26, 9) | Silver Mini | 42 | 25.8% | 1.17 | **6.54** | 33.4% | 47.6% |

**#1 — MACD Trend (5,13,5) / Gold Mini**: the strongest result in the entire sweep once growth is
weighed in — highest Sharpe *and* highest CAGR of all 112 runs, on a large sample (91 trades). The
37% drawdown is real and needs real risk tolerance, but it's earned alongside the strongest overall
performance, not sitting on a mediocre one.

**#2 — RSI Mean-Reversion (7, 30/70) / Gold Mini**: second-best growth, highest win rate (77%) of any
strong performer. A completely different strategy logic (mean-reversion vs. MACD's momentum) also
working well on Gold is a good robustness sign for the instrument itself, not just one strategy.

**#3 / #4 — MACD Trend / Copper (two parameter variants)**: a real step down in growth from Gold, but
meaningfully smaller drawdowns (25-27% vs. 37-40%). #4 has the largest sample size of the whole list
(91 trades) — worth taking seriously precisely because it's the least likely to be a fluke.

**#5 — MACD Trend (12,26,9) / Silver Mini**: the highest profit factor of the top 5, and MACD's third
appearance across three different metals (Gold, Copper, Silver) — that cross-commodity consistency
for one strategy family is a stronger signal than any single number.

**The strategy-family headline**: MACD Trend appears **3 of the top 5** times, across 3 different
commodities. Across the full 112-run sweep, MACD Trend and RSI Mean-Reversion — both added this
session — turned out more broadly reliable than the original two strategies (SMA Crossover, Donchian
Breakout), which would never have been discovered by testing only those two.

## What *not* to conclude from this

- **Aluminium Mini looked best on risk-adjusted metrics alone** (Sharpe, drawdown, profit factor —
  several strategies clean and consistent there), but its CAGR tops out around 7.6%, an order of
  magnitude below Gold's. This is a real trade-off, not a contradiction: Aluminium Mini's contract is
  much smaller notional value (~₹2-2.5L/lot) than Gold Mini's (~₹7-15L/lot), and the backtest engine
  trades exactly 1 lot per signal regardless of capital — so a clean edge on a small-notional
  instrument doesn't compound into much absolute growth. If the goal is a smooth, low-drawdown ride
  rather than maximum growth, Aluminium Mini's results (see the dashboard) are worth a second look.
- **"1 lot regardless of instrument" is not a margin-aware, capital-normalized position-sizing rule.**
  A fair CAGR comparison across commodities with very different lot notional values would need each
  instrument sized to use a consistent amount of capital/margin — not built yet. Treat this ranking as
  directionally right, not a precise apples-to-apples growth comparison.
- **All 5 of the current top picks concentrate in 2 commodities (Gold, Copper/Silver metals).** Picking
  several of these together does not diversify risk — they're correlated exposures to the same
  underlying commodity moves, and this window includes the historic Jan 2026 gold/silver crash.
- **112 combinations were tested; the "top 5" is inherently subject to the multiple-comparisons
  trap.** No train/test split or walk-forward validation has been run yet — see
  `docs/pending-actions.md`.

## Methodology

- Ranking excludes any result with fewer than 15 closed trades, or with max drawdown over 50% — both
  flagged (not silently dropped) on the dashboard and in `bot/growmore_bot/backtest/run_all.py`.
- `profit_factor = None` in the database means "infinite" (zero losing trades in the sample), not
  zero — a real bug found and fixed this session (see `docs/technical-debt.md`).
- Full parameter grid, all 5 strategy definitions, and the ranking/flagging logic live in
  `bot/growmore_bot/backtest/run_all.py` and `bot/growmore_bot/strategies/`.
- Re-run: `python -m growmore_bot.backtest.run_all --from-date 2021-09-04 --to-date 2026-09-03`
  (from `bot/`, with `DATABASE_URL`/`DHAN_*` env vars set — see `bot/README.md`).

See `docs/pending-actions.md` for what's still needed before any of this influences real paper
trading (reviewing/enabling via the dashboard's Strategy Config page, setting real risk limits).
