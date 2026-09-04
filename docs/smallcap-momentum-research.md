# Small-Cap Momentum/Breakout Strategy Research (2026-09-04)

Research only — **no code has been written yet**. Produced via two codebase-exploration passes
(Dhan client / strategy / backtest engine reusability), a round of web research (NSE Indices' own
momentum-index methodologies, academic momentum literature, practitioner breakout methodology, NSE
circuit-filter rules, survivorship-bias practice), and one real, read-only verification call against
Dhan's live API (see Data feasibility below).

The question this answers: for a momentum/breakout strategy on Indian small-cap equities (Nifty
Smallcap 250, possibly Nifty Midcap 150), which strategy *type* actually fits, and can this bot's
existing Dhan/backtest infrastructure support it.

## Strategy taxonomy

| Approach | Shape | Real-world precedent |
|---|---|---|
| **Cross-sectional (relative) momentum** | Rank the whole universe by risk-adjusted trailing return, hold the top N, rebalance periodically | Jegadeesh & Titman (1993) — the foundational momentum result (~1.5%/month winners-minus-losers, later replicated globally). NSE's own **Nifty200 Momentum 30** index: normalized momentum score = 6-month + 12-month price return, **adjusted for daily price volatility**; rebalanced semi-annually (Jun/Dec); weight = free-float mcap × momentum score, capped at 5%. |
| **Cross-sectional momentum + quality, small-cap-specific** | Same as above, restricted to Smallcap 250, plus a quality filter | NSE's **Nifty Smallcap250 Momentum Quality 100**: momentum score (6m/12m return, vol-adjusted) combined with a quality score (ROE, debt/equity, 5-year EPS growth stability); rebalanced semi-annually; a stock dropping out of Smallcap 250 drops out of this index too. The single most directly relevant, already-live methodology for this exact universe. |
| **Per-stock breakout (time-series)** | A single stock's own price crossing a threshold (its own N-day high, a channel, an MA cross) | Turtle-style Donchian breakout (a 52-week-high variant showed ~14% average 6-month excess return over Nifty 50 in one 2010–2025 India-specific study); Minervini's VCP (breakout from a volatility-contraction base within an existing uptrend, entry on a volume surge ≥140–150% of average, stop defined at the base) — small/mid-cap-oriented by design. |
| **Per-stock trend-following** | MACD / moving-average crossover | Already implemented (`macd_trend.py`, `sma_crossover.py`) — directly applicable per stock, no new code needed to just run one. |
| Mean-reversion (RSI, Bollinger) | Fades extremes | Already implemented but the **wrong shape** for a momentum/breakout thesis — useful only as a contrast/robustness check. |

**Which is ideal:** this splits into a **selection question** (which stocks, out of ~250, deserve
capital at all?) and a **timing question** (is this individual stock breaking out right now?). This
bot's existing strategies already answer the timing question well. They have **no answer at all**
for the selection question — see Fit against the codebase below. NSE running two live indices on
exactly this idea is strong evidence that, for this universe, professional practice favors
**cross-sectional selection first, with per-stock trend/breakout math as the entry-timing layer
within the selected set** — not one indicator run blindly across all 250 names.

## Small-cap-specific risk factors

- **Liquidity / circuit filters**: NSE bands are 2%/5%/10%/20%, tightened for higher-volatility
  names — exactly the small-cap population. A small-cap can lock limit-up/down and simply not fill;
  a backtest assuming any-price fills materially overstates real performance. Midcap 150
  (Sharpe ≈0.45) has meaningfully better risk-adjusted returns and liquidity than Smallcap 250
  (Sharpe ≈0.35) per multi-year index comparisons — worth backtesting **both universes side by
  side**, not committing to Smallcap 250 alone up front.
- **Survivorship bias**: testing only *today's* 250 constituents against history excludes delisted/
  demoted names and has been shown to overstate annualized returns by roughly 5 percentage points and
  inflate Sharpe in Indian-market reconstructions. Point-in-time historical constituent lists are
  **not available from Dhan** (confirmed below) — a real, named gap, not silently ignored.
- **Position sizing across wildly different prices**: the existing `BacktestEngine` trades "1 lot"
  regardless of capital (already flagged in `docs/backtest-results.md` for commodities) — for 250
  differently-priced stocks this bias is worse, not better. Needs capital- or volatility-normalized
  sizing (e.g. fixed-rupee or ATR-based sizing) before cross-stock rankings are trustworthy.

## Fit against the existing codebase

- **`BacktestEngine`** (`bot/growmore_bot/backtest/engine.py`) is genuinely asset-agnostic at its
  core (replays any OHLC series, fills at next-bar open) — long-only (fine for this thesis),
  single-instrument-per-run, no portfolio/ranking state, `lot_size` scaling defaults harmlessly to 1
  for equities.
- **`Strategy.on_bar(bar, position_state)`** (`strategies/base.py`) is single-instrument by
  construction — it can run per-stock today (as the commodity sweep already does per-instrument),
  but has **no cross-sectional ranking concept** ("top N by momentum score this month"). Building the
  NSE-style selection layer above is genuinely new code, not a parameter tweak to an existing
  strategy.
- **`run_all.py`'s sweep pattern** (grid of strategy variants × instruments, ranked with a minimum
  trade-count filter and explicit multiple-comparisons caveats, per `docs/backtest-results.md`)
  transfers directly to a larger stock universe — the same reporting rigor should be reused. At
  ~250 stocks × several variants this is thousands of runs; worth budgeting real wall-clock/API-rate
  time rather than assuming it's as fast as the 112-run commodity sweep.

## Data feasibility — verified live against Dhan's real API (2026-09-04)

- **NSE equity historical data works today with the existing `DhanClient` code, zero changes
  needed.** `exchange_segment`/`instrument_type` were already generic pass-throughs (never hardcoded
  to `MCX_COMM`); confirmed live: `get_historical_ohlc(security_id="8954"` [TTML — Tata Teleservices
  Maharashtra]`, exchange_segment="NSE_EQ", instrument_type="EQUITY")` returned **4,134 real daily
  bars from 2010-01-03 to 2026-08-30** (16+ years — deeper than the 5-year MCX depth used so far),
  each with a real `volume` field (needed for liquidity filtering). `get_quote` and
  `get_fund_limits` also work unchanged against the same `NSE_EQ` segment.
- **No index-constituent API.** Neither Dhan's SDK nor its public instrument-master CSV
  (`images.dhan.co/api-data/api-scrip-master.csv`, confirmed downloadable, ~200k rows) expose "give
  me today's Nifty Smallcap 250 members," let alone a historical/point-in-time version. The current
  member list would need sourcing externally (e.g. NSE Indices' own published index constituent
  files) — free and public, but a manual/periodic pull, not an API call this bot already makes.
  Point-in-time historical constituents (needed to fully avoid survivorship bias) are **not freely
  available anywhere identified in this research** — an accepted limitation for an initial pass, not
  a blocker.

## Recommendation

1. **Target shape**: cross-sectional momentum selection (approximating NSE's own Nifty
   Smallcap250 Momentum Quality 100 methodology — 6m/12m vol-adjusted return, semi-annual rebalance)
   as the stock **selection** layer, with the existing Donchian-breakout/MACD-trend strategies
   reused as the **entry-timing** layer within whatever's selected — not a single indicator run
   blindly across 250 names.
2. **Universe**: backtest Smallcap 250 and Midcap 150 side by side, not a single up-front choice —
   the liquidity/Sharpe difference is large enough that this should be an empirical result.
3. **Data**: Dhan's Data API, exactly as already used for commodities — confirmed working, no new
   integration needed. Source the current constituent list from NSE Indices' public factsheets;
   explicitly carry the survivorship-bias limitation rather than solving it in this pass.
4. **Before trusting any cross-stock ranking**: fix position sizing to be capital- or
   volatility-normalized (not "1 lot"), matching the lesson already learned from the commodity
   lot-size bug (`docs/technical-debt.md`).

## What *not* to conclude from this

- This is a desk-research pass, not a backtest — no historical performance numbers for this
  strategy/universe combination exist yet. Every "ideal for these stocks" claim above rests on NSE's
  own published methodology and third-party India-specific studies, not this bot's own data.
- Confirming Dhan's `NSE_EQ` data works is not the same as having validated liquidity/circuit-filter
  behavior in it — the one real call made here checked depth and shape, not fill realism under a
  circuit lock.

## Next steps (not started)

- A follow-up, separately-scoped planning pass would design: the cross-sectional selection
  strategy's actual implementation (new code beyond `Strategy.on_bar`), the constituent-sourcing
  pipeline, capital-normalized position sizing in `BacktestEngine`, and the concrete backtest sweep
  (universe × strategy variant grid, minimum-trade-count filters, reporting format matching
  `docs/backtest-results.md`) — deliberately deferred until this research is confirmed as the right
  direction.

## Sources

- NSE Indices, [Nifty200 Momentum 30 Index whitepaper](https://www.niftyindices.com/docs/default-source/indices/nifty200-momentum-30-index/nifty200_momentum_30_index_whitepaper_sep_20.pdf)
- NSE Indices, [Nifty Smallcap250 Momentum Quality 100 factsheet](https://www.niftyindices.com/Factsheet/Factsheet_NiftySmallcap250MomentumQuality100.pdf)
- Jegadeesh & Titman, "Momentum" (foundational 1993 result and later reviews) — [Tulane summary](https://breesefine7110.tulane.edu/wp-content/uploads/sites/16/2015/10/Momentum-2001.pdf), [Springer 30-years-later review](https://link.springer.com/article/10.1007/s11408-022-00417-8)
- [Nifty 50 vs Midcap 150 vs Smallcap 250 performance/Sharpe comparison — Personal Finance Plan](https://personalfinanceplan.in/nifty-50-vs-midcap-150-vs-smallcap-250-vs-nifty-500-cap-based-indices-performance-comparison-2005-2026/)
- [NSE individual stock circuit filter bands — Oquilia](https://www.oquilia.com/news/nse-individual-stock-circuit-filter-bands)
- [Survivorship-bias-free NIFTY backtesting — Marketcalls](https://www.marketcalls.in/llm-models/how-to-build-survivorship-bias-free-nifty-50-historical-data-using-grokbot.html), [sharpely.in point-in-time data](https://sharpely.in/blogs/bias-free-backtesting-explained-sharpely-uses-point-time-data-avoid-look/)
- Minervini VCP methodology — [tradingmomentum.substack.com](https://tradingmomentum.substack.com/p/the-volatility-contraction-pattern-b57), [TrendSpider](https://trendspider.com/learning-center/volatility-contraction-pattern-vcp/)
- [52-week-high breakout, India-specific study summary — Swastika](https://www.swastika.co.in/blog/52-week-high-breakout-strategy-in-2026-how-traders-spot-momentum-stocks-should-you-buy-them)
- [Dhan historical data / instrument list docs](https://dhanhq.co/docs/v2/historical-data/), [Dhan instrument master CSV](https://dhanhq.co/docs/v2/instruments/)
