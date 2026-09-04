# Small-Cap Momentum Backtest Results — Real Data (2026-09-04)

Real backtest, not a projection: 5 years of actual Dhan daily price data (2021-09-04 to 2026-09-04),
400 real stocks (Nifty Smallcap 250 + Nifty Midcap 150, 100% matched to Dhan security IDs, zero
unmatched), best-effort real fundamentals via yfinance (83.5% coverage, 334/400 stocks). Full
methodology in `docs/smallcap-momentum-research.md`; code in `bot/research/smallcap_momentum/`.
Live on the dashboard's [Smallcap tab](../dashboard/app/smallcap/page.tsx).

**Headline finding, upfront: the momentum strategy did not clearly beat simply buying and holding
everything.** See "The benchmark that matters" below before reading anything else here as a
recommendation to act on.

## Results

| Universe | Variant | CAGR | Sharpe | Max drawdown | Win rate | Quality coverage |
|---|---|---|---|---|---|---|
| Smallcap 250 | Momentum only | **26.2%** | 1.31 | 27.9% | 61.3% | — |
| Smallcap 250 | Momentum + quality | 25.8% | 1.30 | 28.1% | 60.8% | 82.7% |
| Smallcap 250 | Momentum + quality + trend | 25.9% | 1.32 | 27.5% | 60.8% | 86.0% |
| Midcap 150 | Momentum only | 23.7% | 1.38 | 26.2% | 62.5% | — |
| Midcap 150 | Momentum + quality | 23.0% | 1.42 | 26.6% | 64.2% | 85.4% |
| Midcap 150 | Momentum + quality + trend | 23.4% | **1.43** | 26.7% | 63.7% | 83.1% |

10 rebalances each (semiannual, Jun/Dec 2021–2026), 30-stock equal-weight portfolio, ₹10,00,000
starting capital, 240 holding periods measured per run.

## The benchmark that matters

A naive **equal-weight buy-and-hold of the entire universe** (no ranking, no rebalancing beyond
dropping stocks with too much missing data — 193/250 Smallcap names, 128/150 Midcap names survived
that filter) over the identical window:

| Universe | CAGR | Sharpe | Max drawdown |
|---|---|---|---|
| Smallcap 250 (buy everything) | **28.0%** | 1.27 | 28.3% |
| Midcap 150 (buy everything) | **26.5%** | 1.37 | 24.2% |

**The momentum strategy's CAGR is lower than just buying the whole universe, in both cases** —
26.2% vs. 28.0% (Smallcap), 23.7% vs. 26.5% (Midcap). Sharpe is roughly a wash (momentum edges it out
slightly on Smallcap, buy-and-hold edges it out on Midcap's drawdown). The quality and trend-filter
overlays make essentially no difference either way (all six variants land within ~1 CAGR point of
each other) — the coverage numbers above confirm the overlays weren't starved of data (82–86%
fundamentals coverage), they just didn't move the outcome much.

**Read this honestly, not as "momentum failed."** 2021–2026 was an exceptional, broad-based bull run
for Indian small/mid caps — a 26–28% *buy-everything* CAGR is not a normal baseline to beat. A
selection strategy's real value usually shows up in *avoiding the worst names during the drawdown*,
not in a raw CAGR race during an unusually strong up-trend across nearly the whole universe. This
backtest, as built, doesn't isolate that — it only measures raw return/risk over one continuous
window. A regime that includes a real, sustained small-cap bear market (not just the drawdown blips
inside this mostly-up window) is needed before concluding either way.

## What this does and doesn't prove

- **Does show**: real, working infrastructure — a genuine cross-sectional momentum(+quality)
  backtest running on 400 real Indian stocks with real Dhan price data and real (yfinance-derived)
  fundamentals, end to end, persisted and viewable on the dashboard. The selection logic is sane
  (spot-checked the final rebalance by hand: HFCL, Ather Energy, Syrma SGS, Kirloskar Oil Engines
  topped the real June 2026 ranking — all genuinely strong 2026 momentum names, not noise).
- **Doesn't show**: that this specific strategy is worth trading. It underperformed the simplest
  possible alternative (buy everything) on the metric that matters most (CAGR), over the one window
  tested.
- **Two real bugs were found and fixed while producing these numbers** (both now covered by
  regression tests in `tests/research/test_portfolio_engine.py`): (1) a rebalance with zero eligible
  stocks (inevitable at the very start of the backtest, before 12 months of price history
  accumulates) was permanently zeroing the entire equity curve instead of holding cash — the first
  full run produced all-zero CAGR/Sharpe/drawdown before this was caught; (2) a rebalance date that
  didn't land exactly on a real trading day (e.g. a semiannual boundary falling on a weekend) was
  silently skipped instead of snapping to the nearest prior trading day — dropped 3 of 10 intended
  rebalances in the first run. Separately, yfinance's `.info` no longer populates `returnOnEquity` at
  all (confirmed against several real tickers) — ROE is now derived from `netIncomeToCommon` /
  (`bookValue` × `sharesOutstanding`) instead, which took fundamentals coverage from 5% to 83.5%.

## Caveats (carried from docs/smallcap-momentum-research.md, still apply)

- **Survivorship bias, not solved.** Today's 250/150 constituents were used for the full 5-year
  window — a stock that got promoted into or demoted out of the index mid-period isn't handled.
  Given the "buy everything" benchmark used the *same* current constituent list, this bias likely
  affects both the strategy and its benchmark similarly, but it isn't zero.
- **Quality/momentum scoring are documented simplifications** of NSE's own (not fully public)
  methodology — see `scoring.py`'s module docstring for exactly what's simplified.
- **Equal weight, not free-float-market-cap weighted** (unlike NSE's real indices) — deliberate, and
  it directly avoids the "1 lot regardless of capital" bias flagged in `docs/backtest-results.md` for
  the commodity sweep.
- **No transaction costs, slippage, or circuit-filter modeling.** A real semiannual rebalance of a
  30-stock small-cap portfolio would incur real impact costs this backtest doesn't charge for —
  applying some would lower every number above, momentum and benchmark alike.
- **One window, one regime.** 10 rebalances over a single continuous bull market is not enough to
  draw a robust conclusion either way — flagged, not solved, here.

## Methodology / re-run

```bash
# from bot/, with the `research` extra installed (pip install -e ".[dev,research]")
python -m research.smallcap_momentum.run_backtest --from-date 2021-09-04 --to-date 2026-09-04
# review bot/research/.output/smallcap_momentum/summary.csv, then:
python -m research.smallcap_momentum.run_backtest --from-date 2021-09-04 --to-date 2026-09-04 --persist
```

Price/fundamentals fetches are cached locally (`bot/research/.cache/`, gitignored) — a re-run skips
anything already fetched. `--persist` writes to the real Neon database
(`portfolio_backtest_runs`/`portfolio_equity_curve_points`/`portfolio_rebalance_holdings`), read by
the dashboard's Smallcap tab.
