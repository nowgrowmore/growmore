# Backtest Results — Strategy/Parameter Sweep (re-run 2026-09-05, cost-adjusted)

**144 runs, 5 years of real Dhan daily data (2021-09-05 → 2026-09-05), 8 MCX commodities.** This
supersedes the 2026-09-04 table completely. Two things changed, and between them they reorder
almost everything:

1. **Capital is now per-instrument, not one flat ₹5,00,000.** Each instrument's backtest is
   capitalised at one lot's own notional (priced off the *first* bar — a later price would be
   lookahead), so every result runs at exactly 1x leverage and the CAGR column finally measures
   edge rather than contract size. Previously a Copper lot (~₹34 lakh) against ₹5 lakh was ~6.9x
   leverage while a Crude Oil Mini lot (~₹0.86 lakh) was ~0.17x — a 40x spread.
2. **Real MCX transaction costs and slippage are modelled** (`bot/growmore_bot/costs.py`):
   brokerage min(₹20, 0.03%), exchange 0.0026%, CTT 0.01% sell-side, stamp 0.002% buy-side, SEBI
   ₹20/crore, GST 18% on the service charges, plus 2 ticks of slippage per side. `cagr_pct` is now
   **net**; `gross_cagr_pct` and `total_transaction_cost` are stored alongside it.

## Top 12, ranked by net CAGR at 1x leverage

Guardrails as before: ≥15 closed trades, max drawdown ≤50%. 73 of 144 runs pass.

| # | Strategy | Instrument | Trades | Net CAGR | Sharpe | Max DD | PF | Cost drag |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | MACD (12,26,9) | Silver Mini | 43 | **33.1%** | 1.22 | 37.0% | 6.03 | 0.12pp |
| 2 | MACD (5,13,5) | Silver Mini | 88 | 31.4% | 1.24 | 38.1% | 2.63 | 0.27pp |
| 3 | Regime-Switch (ADX+MACD 12,26,9+VWAP/EMA) | Silver Mini | 16 | 28.5% | 1.09 | 45.2% | 8.02 | 0.06pp |
| 4 | Regime-Switch (ADX+MACD 5,13,5+RSI) | Silver Mini | 51 | 28.0% | 1.07 | 41.3% | 2.45 | 0.19pp |
| 5 | Regime-Switch (ADX+MACD 5,13,5+VWAP/EMA) | Silver Mini | 46 | 27.3% | 1.06 | 42.4% | 2.34 | 0.17pp |
| 6 | Regime-Switch (ADX+MACD 12,26,9+RSI) | Silver Mini | 23 | 27.1% | 1.01 | 41.2% | 4.52 | 0.09pp |
| 7 | RSI (7, 30/70) | Nickel | 44 | 22.9% | 0.51 | 21.9% | 2.75 | 0.13pp |
| 8 | MACD (5,13,5) | Gold Mini | 92 | 22.5% | **1.45** | **18.6%** | 3.05 | 0.34pp |
| 9 | RSI (7, 30/70) | Silver Mini | 39 | 21.1% | 0.99 | 37.1% | 2.70 | 0.17pp |
| 10 | Donchian (10) | Gold Mini | 23 | 19.4% | 1.11 | 22.0% | 3.79 | 0.09pp |
| 11 | Regime-Switch (ADX+MACD 5,13,5+VWAP/EMA) | Aluminium Mini | 32 | 18.5% | **1.53** | **6.8%** | 5.38 | 0.27pp |
| 12 | SMA (5, 20) | Gold Mini | 38 | 18.4% | 1.04 | 24.9% | 3.31 | 0.16pp |

### What actually changed, and why

**Copper is gone from the top 12 entirely.** The old table's #2 (Regime-Switch/Copper, 27.1%) and #5
(MACD 12,26,9/Copper, "the highest raw CAGR of the top 5" at 33.0%) were **leverage artifacts**. On
equal risk those same two runs are 10.4% and 13.2%. Copper's best result anywhere in this sweep is
now 13.2%. Nothing about Copper got worse — it was never as good as the table said.

**Silver Mini takes all six top slots**, across three different strategy families. That is a much
stronger cross-strategy signal than the old table's "MACD appears 4 of 5 times", which was partly an
artefact of which contracts happened to be large.

**Regime-Switch is rehabilitated, and `docs/goldmini-regime-switch-results.md` is wrong at the
universe level.** That document concluded regime-switch was "not recommended as built" — but it only
ever tested **Gold Mini**, where the conclusion still holds. Across the universe, 4 of the top 6
results are regime-switch variants on Silver Mini, and **Regime-Switch (5,13,5 + VWAP/EMA) on
Aluminium Mini is the best risk-adjusted result in the entire sweep**: Sharpe 1.53 and a 6.8% max
drawdown, less than half the drawdown of anything else near it, on 32 trades. Single-instrument
negative results should not have been generalised.

**Costs are immaterial for a daily book, exactly as predicted.** The drag is 0.06–0.34 percentage
points of CAGR across the board — these strategies trade 15–90 times in *five years*. This was worth
building for correctness and because it becomes load-bearing for any higher-turnover idea, but
anyone hoping costs explained a disappointing result should stop looking here.

**MACD (5,13,5) / Gold Mini remains the most trustworthy single pick** even though it is only 8th by
CAGR: the best Sharpe of any high-sample result (1.45), the shallowest drawdown of the top 10
(18.6%), and by far the largest sample (92 trades). Rank by confidence rather than by CAGR and it is
at or near the top.

**Lead Mini and Crude Oil Mini are broadly unprofitable** across every strategy family — most of
their runs are negative. Neither currently has an enabled config, and neither should get one.

## Currently-configured strategies, on the new basis

| Config (mode) | Strategy | Trades | Net CAGR | Sharpe | Max DD | PF |
| --- | --- | --- | --- | --- | --- | --- |
| SILVERM (paper) | MACD (12,26,9) | 43 | 33.1% | 1.22 | 37.0% | 6.03 |
| SILVERM (paper) | MACD (5,13,5) | 88 | 31.4% | 1.24 | 38.1% | 2.63 |
| SILVERM (paper) | Regime-Switch (12,26,9+VWAP/EMA) | 16 | 28.5% | 1.09 | 45.2% | 8.02 |
| SILVERM (paper) | Regime-Switch (5,13,5+VWAP/EMA) | 46 | 27.3% | 1.06 | 42.4% | 2.34 |
| GOLDM (paper) | MACD (5,13,5) | 92 | 22.5% | 1.45 | 18.6% | 3.05 |
| GOLDM (paper) | Donchian (10) | 23 | 19.4% | 1.11 | 22.0% | 3.79 |
| COPPER (paper) | MACD (12,26,9) | 50 | 13.2% | 0.98 | 12.6% | 2.99 |
| COPPER (paper) | SMA (5,20) | 37 | 12.2% | 0.85 | 17.3% | 2.39 |
| COPPER (paper) | MACD (5,13,5) | 93 | 11.5% | 0.83 | 16.1% | 1.88 |
| COPPER (paper) | Regime-Switch (12,26,9+RSI) | 19 | 10.4% | 1.06 | 12.7% | 9.33 |
| GOLDM (paper) | VWAP+CPR Session-Bounce | — | **never backtested** | — | — | — |

The four Copper configs are the weakest of the enabled set on equal risk, though all four are
comfortably profitable and none has an alarming drawdown — they are simply not the standouts the
old table implied. `vwap_session_bounce` still has no backtest at all; see
`docs/technical-debt.md` for why that is now fixable and how.

## What *not* to conclude from this

- **These are 1x-leverage numbers.** A 33.1% CAGR on Silver Mini means 33.1% on ~₹12 lakh of
  capital, not on ₹2,50,000. `python -m research.capital.admission` prints what each instrument
  actually needs; at a 2% risk-per-trade budget Silver Mini wants ~₹33 lakh and Copper ~₹40 lakh.
- **144 combinations were tested and the top of the table was picked from them.** No walk-forward or
  out-of-sample validation has been run yet, so selection bias is unquantified. A deflated Sharpe
  calculation is the next item that addresses this.
- **One five-year window, one regime** — a period containing an exceptional precious-metals bull
  run, which is precisely where Silver Mini's dominance comes from. That is a reason to be careful
  about extrapolating slots 1–6.
- **Aluminium Mini's Sharpe 1.53 / 6.8% drawdown is one result out of 144.** It is the most
  interesting thing here and the least proven.
- **Still not modelled:** stop-losses of any kind, position sizing beyond 1 lot, short positions, and
  the daily-loss/expiry force-close guards the live engines apply but the backtest does not.

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
