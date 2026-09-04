# Gold Mini ADX Regime-Switch — Real Backtest Results (2026-09-04)

Real backtest, 5 years of real Dhan daily data (2021-09-04 to 2026-09-04), local Postgres (not
persisted to the real Neon DB — see Methodology). Code in `bot/growmore_bot/strategies/
regime_switch.py`; full design rationale in the (approved) plan this session; see
`docs/smallcap-momentum-backtest-results.md` for the sibling honest-negative-result writeup this
follows the same format/rigor from.

**Headline finding: the regime-switch strategy did not beat the standalone strategies it was built
from — it underperformed on every metric tested, in every variant.** This is a genuine, negative
result. It is reported here in full rather than reframed, per this project's standing practice
(`docs/backtest-results.md`, `docs/smallcap-momentum-backtest-results.md`).

## Results (Gold Mini only, sorted by profit factor)

| Strategy | Trades | Profit factor | Win rate | Sharpe | Max drawdown |
|---|---|---|---|---|---|
| MACD Trend (5,13,5) — standalone | 91 | 3.20 | 53.8% | **1.56** | **37.4%** |
| RSI Mean-Reversion (7,30/70) — standalone | 39 | **3.46** | **76.9%** | 1.22 | 39.6% |
| MACD Trend (12,26,9) — standalone | 51 | 2.75 | 52.9% | 1.25 | 56.6% |
| Regime-switch: ADX + MACD(5,13,5) + VWAP/EMA | 57 | 2.85 | 54.4% | 1.34 | 51.9% |
| Regime-switch: ADX + MACD(5,13,5) + RSI | 54 | 2.18 | 51.9% | 1.14 | 58.8% |
| Regime-switch: ADX + MACD(12,26,9) + VWAP/EMA | 28 | 2.14 | 60.7% | 1.10 | 67.2% |
| Regime-switch: ADX + MACD(12,26,9) + RSI | 27 | 1.91 | 63.0% | 0.92 | 74.4% |

**Every regime-switch variant has a lower Sharpe ratio AND a deeper max drawdown than the standalone
MACD variant it's built from.** The best regime-switch result (ADX + MACD(5,13,5) + VWAP/EMA: Sharpe
1.34, 51.9% drawdown) still loses to plain MACD(5,13,5) alone (Sharpe 1.56, 37.4% drawdown) on both
of the metrics that matter most for real risk-taking.

## Why this probably happened (reasoned, not re-tested here)

- **The regime filter itself lags.** ADX only confirms a trend has started (or a range has set in)
  *after* it's already partway underway — the exact whipsaw-at-transitions cost flagged before this
  was ever built. Layering a lagging filter on top of MACD (which is itself already a lagging
  indicator) compounds the lag rather than fixing MACD's weak points.
- **Fewer trades, not better ones.** The MACD(12,26,9) pairings dropped from 51 standalone trades to
  27-28 gated trades — the ADX gate filtered out real signals, but the ones that survived weren't
  better on average: drawdown got *worse* (56.6% → 67-74%) despite trading less.
  Being more selective didn't mean being more right.
- **RSI/VWAP-EMA ranging-mode entries may have been suppressed at exactly the wrong moments** — a
  genuine range-bound bounce that would have worked never got the chance to fire if ADX had already
  (correctly or not) classified the market as "trending" at that moment, keeping MACD in control.

None of this was re-verified in isolation — it's the most plausible reading of the numbers, not a
proven root cause. A genuine follow-up would need to inspect the actual rebalance-by-rebalance regime
transitions against price action, which wasn't done here.

## What this does and doesn't mean

- **Does mean**: this specific regime-switch design, with these specific thresholds (ADX 14-period,
  enter-trending at 25, exit at 20) and these specific sub-strategy pairings, is not an improvement
  over just running MACD(5,13,5) alone on Gold Mini. **Not recommended for paper or live trading as
  built.**
- **Doesn't mean**: regime-switching as an idea is wrong, or that ADX is the wrong regime detector —
  only that *this* combination, on *this* instrument, over *this* window, didn't work. Untested
  variations that might do better: looser/tighter hysteresis bands, a different regime detector
  (e.g. price-vs-200-day-MA instead of ADX), or applying the same idea to a different instrument where
  the two component strategies' historical edge was more cleanly separated by regime in the first
  place (this session's earlier finding — that MACD and RSI both scored well on Gold Mini
  historically — was read as evidence of *alternating* regimes, but this result suggests the two
  strategies may have simply both been generically decent on this instrument, not cleanly
  regime-separated after all).
- The infrastructure itself is real and correct — `_AdxCalculator` matches an independently computed
  reference exactly (see `tests/unit/test_regime_switch.py`), both sub-strategies stay warm while
  inactive, and hysteresis behaves as designed. The idea was tested fairly; it just didn't win.

## What's not covered by this result

- **`VwapSessionBounceStrategy`** (the live CPR+VWAP intraday strategy) has **no backtest** — it
  can't have one, by design (see its module docstring). It's validated separately, by paper trading.
- No transaction costs or slippage modeled, same caveat as every other backtest in this repo.
- Single window, no walk-forward or out-of-sample split — same multiple-comparisons caveat as
  `docs/backtest-results.md`.

## Methodology / re-run

Results computed against **local Postgres**, not the real Neon database — following this project's
standing "review locally before persisting" pattern (`docs/backtest-results.md`,
`docs/smallcap-momentum-backtest-results.md`). Given the negative result, nothing here is being
persisted to Neon or enabled for paper trading.

```bash
docker run -d --name growmore-test-pg -e POSTGRES_PASSWORD=postgres -p 5433:5432 postgres:16
docker exec growmore-test-pg psql -U postgres -c "CREATE DATABASE growmore_test;"
DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5433/growmore_test" alembic upgrade head
# seed a GOLDM instruments row from config.DEFAULT_COMMODITY_UNIVERSE, then:
DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5433/growmore_test" \
  python -m growmore_bot.backtest.run_all --from-date 2021-09-04 --to-date 2026-09-04
```
