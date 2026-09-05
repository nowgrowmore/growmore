# Backtest Results — Strategy/Parameter Sweep (definitive re-run, 2026-09-05)

> ## ⚠️ SUPERSEDED — every `risk_managed` number below is wrong, and the ranking is inverted
>
> Read `docs/walk-forward-results.md`, `docs/currency-decomposition-results.md` and
> `docs/phase4-oos-results.md` first. Three things happened after this table was written:
>
> 1. **A same-bar lookahead was found in the trailing stop** (`risk/wrapper.py`, fixed
>    2026-09-05 evening). Every `risk_managed` row here is affected. Gold Mini's headline
>    falls **1.79 → 1.62**; Silver Mini's rises **1.33 → 1.49**, and its best variant becomes
>    **39.3% CAGR / 1.52 Sharpe / 16.3% DD**. See `docs/technical-debt.md`.
> 2. **No backtest here carried a buy-and-hold control.** With one, holding the contract beats
>    the trading system on mean out-of-sample Sharpe (1.17 vs 0.71) and on five of eight
>    instruments. On Gold Mini specifically the strategy returns 109% where holding returned
>    161%; what it buys is a smaller drawdown, not edge.
> 3. **A third of Gold Mini's Sharpe is the rupee, not gold.** USD/INR went 72.98 → 95.38 over
>    the window. Re-denominated in USD, Gold Mini's headline drops 1.62 → 1.10 while Silver
>    Mini's holds at 1.52 → 1.40.
>
> **Net effect: the DSR column below has Gold Mini and Silver Mini backwards.** Gold Mini's
> "significant" 0.96 is largely currency plus a bull market; Silver Mini's "luck" 0.73 is the
> one instrument here that genuinely beats buy-and-hold on return, Sharpe and drawdown at once.
>
> Also: the sweep's single `significant` result by DSR — Bollinger(20,2.5) on Zinc Mini —
> closes **six** trades in its risk-managed form, under this repo's own 15-trade guardrail.

**216 runs, 5 years of real Dhan daily data (2021-09-05 → 2026-09-05), 8 MCX commodities, 128
passing the guardrails.** This supersedes every earlier table. Four things changed since the
2026-09-04 version, and together they reorder the results and change which one you should trust:

1. **Capital is per-instrument.** Each backtest is capitalised at one lot's own notional (priced off
   the *first* bar — a later price would be lookahead), so every result runs at 1x leverage. Under
   the old flat ₹5,00,000 a Copper lot was ~6.9x leverage and a Crude Oil Mini lot ~0.17x, so the
   CAGR column ranked contract size as much as edge.
2. **Real MCX costs and slippage** are charged per leg (`bot/growmore_bot/costs.py`). `cagr_pct` is
   net; `gross_cagr_pct` and `total_transaction_cost` sit beside it.
3. **The price series is repaired.** Dhan's daily history contains unusable bars (`open=high=low=0`)
   and, more seriously, **overlaps two contract months at every roll** — 43 repeated dates for Gold
   Mini alone, mostly with different prices and volumes. Duplicates now resolve to the
   higher-volume (liquid front-month) bar. Every number published before today sat on a series with
   roughly a dozen fake ~1% gaps a year.
4. **Two new strategy families**: an ATR stop/trail risk layer that wraps any strategy, and a
   five-speed MACD ensemble.

## Ranked by Sharpe, which is the leverage-invariant column

| # | Strategy | Instrument | Trades | Net CAGR | Sharpe | Max DD | PF | DSR |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | **Risk-managed ensemble** (5 MACD speeds, 2×ATR stop, 3×ATR trail) | Gold Mini | 47 | 22.2% | **1.79** | **10.0%** | 3.90 | **0.96** |
| 2 | Risk-managed MACD (5,13,5) | Gold Mini | 86 | 22.3% | 1.70 | 8.8% | 2.97 | 0.92 |
| 3 | Bollinger (20, 2.5) | Zinc Mini | 21 | 14.2% | 1.63 | 11.8% | — | 0.92 |
| 4 | Risk-managed MACD (12,26,9) | Gold Mini | 45 | 19.2% | 1.56 | 10.3% | 3.40 | 0.88 |
| 5 | MACD (5,13,5) — *the incumbent* | Gold Mini | 86 | 22.0% | 1.43 | 19.1% | 3.00 | 0.76 |
| 6 | Risk-managed MACD (5,13,5) | Silver Mini | 88 | 32.6% | 1.33 | 28.4% | 2.57 | 0.73 |
| 7 | Regime-Switch (5,13,5+VWAP/EMA) | Aluminium Mini | 38 | 16.2% | 1.29 | 7.4% | 4.21 | 0.71 |
| 8 | MACD (5,13,5) | Silver Mini | 88 | 31.7% | 1.27 | 37.7% | 2.69 | 0.67 |
| 9 | MACD (12,26,9) | Silver Mini | 42 | **33.4%** | 1.24 | 36.8% | 6.30 | 0.65 |
| 10 | Ensemble (bare, no stops) | Silver Mini | 49 | 32.9% | 1.22 | 35.7% | 4.58 | 0.64 |

**DSR is the Deflated Sharpe Ratio** — the probability the result reflects real edge rather than
being the luckiest of everything tried. 216 runs are only ~15 *effective* trials once you account
for how correlated they are, and the luckiest of 15 would post Sharpe ~0.97 by chance. **Exactly one
result clears the conventional 0.95 bar.**

### The headline

**Risk-managed ensemble on Gold Mini is the only statistically significant result in the sweep.**
Sharpe 1.79, a 10.0% max drawdown, 22.2% CAGR at 1x leverage, 47 trades, DSR 0.96.

What makes it interesting is that **neither half is best alone**. The bare ensemble on Gold Mini is
Sharpe 0.98 with a 29.2% drawdown — *worse* than the single luckiest MACD variant, exactly as
expected, because an ensemble trades away the lucky tail. Bare MACD with stops reaches 1.70 but only
DSR 0.92. The combination wins, and it wins on the measure that accounts for how much was tried.

The two ingredients each attack a different problem: **the ensemble removes the parameter choice**
(no lookback selected, so no selection bias), and **the stops cut drawdown roughly in half**
(19.1% → 8.8% on the same MACD entries). Note that ranking by CAGR would have picked #9, MACD
(12,26,9) on Silver Mini at 33.4% — a real result, but with a 36.8% drawdown and DSR 0.65.

### What else changed

- **Copper's old #2 and #5 placings were leverage artifacts.** Its best result here is 13.1%. It is
  perfectly tradeable, just never the standout the flat-capital table implied.
- **Regime-Switch is partly rehabilitated.** `docs/goldmini-regime-switch-results.md` concluded "not
  recommended" from Gold Mini alone, where that still holds — but on Aluminium Mini it posts the
  second-shallowest drawdown in the table (7.4%) at Sharpe 1.29. A single-instrument negative result
  should not have been generalised.
- **Costs are immaterial for a daily book**: 0.06–0.34 percentage points of CAGR. Worth having for
  correctness, and load-bearing for anything higher-turnover, but not the explanation for any
  disappointing result.
- **Lead Mini and Crude Oil Mini are broadly unprofitable** across every family. Neither has an
  enabled config; neither should get one.
- **Shorting was built and measured, and it is worse here.** Sharpe fell in all nine pairings tested
  and the #1 result above collapses to Sharpe 0.15 with a 64% drawdown. 2021–2026 was a secular
  precious-metals bull market. Left behind a default-off flag; see `docs/technical-debt.md`.

## What *not* to conclude

- **These are 1x-leverage numbers.** 22.2% on Gold Mini means 22.2% of ~₹15.3 lakh, not of ₹2.5
  lakh. Run `python -m research.capital.admission` for what each instrument actually needs — at a 2%
  risk-per-trade budget Gold Mini wants ~₹31 lakh behind one lot.
- **One five-year window, one regime** — and a precious-metals bull run at that, which is where most
  of the Silver Mini and Gold Mini performance comes from.
- **DSR ≥ 0.95 is not proof.** It says one result survives a selection-bias correction on this data;
  it says nothing about a different regime. Walk-forward validation is still not built.
- **Still not modelled:** the daily-loss, expiry and end-of-day guards the live engines apply, and
  per-instrument calibration of the stop multiple (2×ATR helps Gold Mini and Nickel, hurts Aluminium
  Mini and Zinc Mini).

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
