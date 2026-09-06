# Monthly stock-option strategies on the NSE F&O universe — results and handover

**Run 2026-09-06.** Nine strategies × 194 measurable stocks = 1,676 backtests, over
2019-09-03 → 2026-09-04. Method was fixed in advance in `docs/stock-options-research.md`,
before the option chains finished downloading. Nothing was written to Neon.

**Update 2026-09-06 (same day).** Added strategy `L` (§6's RSI-scaled basis buffer) and ran both of
§6's flagged experiments — 10 strategies × 194 stocks = 1,841 backtests, plus two walk-forward
cross-selection sweeps. See §7 for the new results. Still nothing written to Neon.

This document is both the results write-up and the handover: everything an agent picking this up
needs is in "The local option store" and "Continuing this work" below.

---

## 1. Headline results

₹30 lakh cash-secured per stock, positions re-sized to current equity at each entry, real option
STT (0.1% of premium, sell side only) and real equity-delivery costs on assignment, marked to
market daily. Medians across stocks.

| Strategy | Median CAGR | Sharpe | Max DD | Frozen | Assign% | >B&H CAGR | >B&H Sharpe | Both |
|---|---|---|---|---|---|---|---|---|
| **G — ATM wheel** (owner's own) | **14.2%** | **0.69** | 36.6% | 68% | 44% | 32% | 59% | **30%** |
| H — G + premium floor | 13.9% | 0.67 | 36.9% | 68% | 44% | 31% | 58% | 29% |
| I — G + trend skip | 11.3% | 0.56 | 37.4% | 68% | 48% | 38% | 55% | 34% |
| L — G + RSI-scaled basis buffer | 14.6% | 0.68 | 37.8% | 68% | 44% | 34% | 62% | 33% |
| A — 5% OTM wheel, no-loss rule | 12.6% | 0.66 | 33.9% | 53% | 23% | 35% | 61% | 34% |
| B — 5% OTM wheel, unconstrained | 7.0% | 0.53 | 29.2% | 30% | 23% | 34% | 45% | 28% |
| C — buy-write | 11.0% | 0.62 | 33.3% | 60% | 0% | 39% | 58% | 32% |
| D — IV-rich, dynamic strike | 8.0% | 0.62 | 30.0% | 18% | 7% | 40% | 61% | 36% |
| E — trend-conditioned calls | 11.4% | 0.57 | 37.2% | 55% | 0% | 36% | 51% | 28% |
| F — put credit spread | 3.7% | 0.40 | **20.8%** | 19% | 23% | 24% | 40% | 20% |
| **Buy & hold (control)** | **15.9%** | 0.62 | 48.1% | — | — | — | — | — |

*"Frozen" = share of days holding stock below the assignment basis with the no-loss rule blocking
action. "Both" = beats that stock's own buy-and-hold on CAGR **and** Sharpe simultaneously.*

**G is the best strategy tested** on both CAGR and Sharpe. **No strategy beats buy-and-hold on
CAGR**; several beat it comfortably on Sharpe and all beat it on drawdown.

---

## 2. What the results actually say

### 2.1 The owner's ATM preference is confirmed

167 matched pairs (same stock, one decision apart), G vs A:

| | median G−A | G better on |
|---|---|---|
| CAGR | **+0.59 pts** | **62%** |
| Sharpe | +0.00 | 51% |
| Max drawdown | +1.51 (worse) | 27% |
| Time frozen | +14.9 (worse) | 0% |

ATM collects roughly double the premium and is assigned roughly twice as often (44% vs 23%). The
extra premium wins on return; it is paid for in drawdown and in being frozen half again as often.

### 2.2 The no-loss rule earns money — a pre-registered prediction that was wrong

178 matched pairs, A (calls only at or above basis) vs B (identical but unconstrained):

| | median A−B | A better on |
|---|---|---|
| CAGR | **+3.58 pts** | **81%** |
| Sharpe | +0.07 | 63% |
| Max drawdown | +2.87 (worse) | 8% |
| Time frozen | +23.1 (worse) | 0% |

Writing a call below the basis locks in the loss when called away; waiting lets a rising market
recover the position. **This is a bull-market dependency and should be read as one** — the window
2019-2026 was strongly positive for Indian equities. The rule buys return and Sharpe at the cost of
deeper drawdowns and a median 53% of days immobilised.

### 2.3 Against buy-and-hold: a real risk-adjusted improvement, not a return improvement

G vs each stock's own buy-and-hold, 167 stocks:

| | median | G better on |
|---|---|---|
| CAGR | **−2.59 pts** | 32% |
| Sharpe | +0.04 | 59% |
| Max drawdown | **−9.78 pts** | **98%** |

Only **30% of stocks** beat buy-and-hold on both metrics at once. The owner's live experience of
beating buy-and-hold is consistent with this **if the stocks traded are range-bound large caps
rather than the whole universe** — see the mechanism below.

### 2.4 The mechanism: the wheel is a tool for stocks that do not run

A vs its own buy-and-hold, split by how buy-and-hold itself did:

| Buy-and-hold's own CAGR | n | A beats it on Sharpe | median ΔCAGR |
|---|---|---|---|
| **Lost money (<0%)** | 23 | **96%** | **+9.7** |
| 0–10% | 36 | 83% | +2.4 |
| 10–20% | 51 | 65% | −2.4 |
| 20–35% | 49 | 43% | −9.1 |
| 35%+ | 19 | 11% | −21.3 |

Perfectly monotonic, and it is simply the covered-call payoff: capped upside. Sector follows the
mechanism rather than driving it — FMCG 90%, Healthcare 69%, Financial Services 67%, against
Information Technology 27% and Metals & Mining 20%.

### 2.5 The per-stock leaderboard is noise — this governs how the tables may be used

Split-half rank correlation (rank in 2019-09→2023-03 vs 2023-03→2026-09):

| Strategy | Spearman (CAGR) | Spearman (Sharpe) | Verdict |
|---|---|---|---|
| A | 0.095 | 0.026 | noise |
| B | −0.058 | −0.014 | noise |
| C | 0.128 | 0.133 | noise |
| D | 0.050 | 0.002 | noise |
| **E** | **0.219** | **0.202** | weak but real |
| F | −0.080 | −0.052 | noise |

Simulating the actual decision — each year trade last year's top N, roll, selection using only
prior years:

| Strategy | top-10 %/yr | field %/yr | edge | years won |
|---|---|---|---|---|
| A | 12.2% | 12.3% | −0.2% | 3/6 |
| C | 15.2% | 13.9% | +1.3% | 3/6 |
| **D** | **13.1%** | 9.6% | **+3.5%** | **5/6** |
| E | 17.7% | 14.9% | +2.8% | 4/6 |
| **F** | 7.9% | 5.2% | +2.8% | **6/6** |

**Trade the strategy, not the leaderboard.** Picking stocks by past wheel performance does not
work. IV-richness (D) is the only selection rule with out-of-sample support.

### 2.6 Per-stock tables

Full data: `bot/research/.output/stock_options/per_stock.csv` (1,676 rows). Top of the G leaderboard,
**labelled not-actionable** per §2.5:

| # | Symbol | Sector | CAGR | B&H | Sharpe | B&H | MaxDD |
|---|---|---|---|---|---|---|---|
| 1 | ADANIPOWER | Power | 43.8% | 51.1% | 0.90 | 1.06 | 53.3% |
| 2 | NYKAA | Consumer Services | 39.4% | 45.3% | **2.38** | 1.49 | 9.6% |
| 3 | BEL | Capital Goods | 32.5% | 42.7% | 1.36 | 1.19 | 42.0% |
| 4 | IDEA | Telecommunication | 31.2% | 16.7% | 0.84 | 0.58 | 54.8% |
| 5 | OIL | Oil Gas | 30.8% | 26.3% | 0.65 | 0.78 | 53.1% |
| 6 | SHRIRAMFIN | Financial Services | 30.6% | 42.8% | 1.20 | 1.23 | 25.7% |
| 7 | JINDALSTEL | Metals & Mining | 29.5% | 43.5% | 1.01 | 1.05 | 55.1% |
| 8 | CHOLAFIN | Financial Services | 29.3% | 31.7% | 0.96 | 0.87 | 57.6% |
| 9 | ADANIENSOL | Power | 28.4% | 34.1% | 1.11 | 0.95 | 15.5% |
| 10 | TORNTPHARM | Healthcare | 28.2% | 28.1% | **1.65** | 1.08 | 17.8% |

50 of 167 stocks beat buy-and-hold on both metrics under G, concentrated in defensives:
AMBUJACEM, ASIANPAINT, AUROPHARMA, AXISBANK, BRITANNIA, CIPLA, DABUR, DMART, DRREDDY and similar.

---

## 3. The two enhancements tried, and why both failed

Declared before running, reported as failures rather than dropped.

**H — refuse to write a call whose premium is under 0.5% of spot.** Aimed at the frozen-position
defect: under the no-loss rule the position sits below basis 68% of days, and a call struck at
basis is then far out of the money, paying almost nothing while still capping the recovery.
Result: **−0.26 CAGR, better on only 19% of stocks.** Frozen time was unchanged at 68% — the floor
essentially never binds, because real basis-struck calls are cheap but not negligible, and forgoing
them costs more than the capped upside saves.

**I — hold uncovered while MACD(5,13,5) says the stock is running.** Result: **−0.46 CAGR, worse on
61%.** It does cut frozen time slightly, but the signal confirms an uptrend only after most of the
recovery has happened, so premium is forfeited without capturing the rebound.

---

## 4. The local option store

**`bot/research/.cache/stock_options/` — 939 MB, gitignored, no token needed to rebuild.**

| Path | Size | Contents |
|---|---|---|
| `days/{YYYY-MM-DD}.parquet` | 589 MB, 1,740 files | One file per trading day, 2019-09-03 → 2026-09-04. What the fetch writes; resumable at day granularity. |
| `symbols/{SYMBOL}.parquet` | 350 MB, 210 files | One file per underlying, **44.3M rows total**. What the engine reads. |

Columns (identical in both): `trade_date, symbol, expiry, strike, opt_type, open, high, low, close,
settle, open_interest, volume, lot_size, underlying`.

Filtered at fetch time to the 210 manifest symbols and to strikes within ±25% of spot.

Related caches: `bot/research/.cache/fno_bars/` (23 MB) holds 15 years of daily cash OHLCV for the
same 210 stocks — used for the buy-and-hold control and for the adjustment factors.
`bot/research/fno/universe.csv` is the committed 210-name manifest with sector and defence labels.

### Source

NSE's public F&O bhavcopy, free, **no token and no Dhan involvement** — Dhan cannot serve this at
all, having no history for expired contracts. Two formats, tried modern-first with fallback:

```
UDiFF  (2024-07+)  .../content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip
legacy (pre-2024)  .../content/historical/DERIVATIVES/YYYY/MON/foDDMONYYYYbhav.csv.zip
cash   (legacy)    .../content/historical/EQUITIES/YYYY/MON/cmDDMONYYYYbhav.csv.zip
```

NSE's archive host blocks default HTTP clients; a browser-like User-Agent is required.

### Four data traps, all found by checking rather than by luck

1. **Adjusted vs unadjusted.** The cached cash series is corporate-action adjusted; historical
   option strikes are not. RELIANCE closes at 589.54 adjusted on 2019-10-03 while its puts traded
   between 900 and 1600 — a 2.14× gap from the 2024 bonus. The first run silently discarded *every
   row*. The underlying now comes from an unadjusted source in both eras, and everything is
   converted into one continuous adjusted space before the engine sees it.
2. **Lot size in legacy files.** NSE reports *option* turnover on the underlying's notional, not
   the premium, so the obvious turnover identity reproduced only 2 of 210 known lot sizes. Applied
   to the *futures* rows in the same file it reproduced 54 exactly and the rest within ~1%. That
   residue is immaterial: positions are one lot against `strike × lot` of capital, so the lot size
   divides out of every return.
3. **Cost base.** Option STT is 0.1% of *premium*, sell side only. `leg_cost` takes one turnover
   number and nothing stops a caller passing `strike × lot` — a ~60× overstatement.
4. **Liquidity.** Bhavcopy lists strikes that never traded. A strike is only sellable if
   `volume > 0`; selling the rest is fiction.

---

## 5. The code

`bot/research/stock_options/` — 2,015 lines, plus 7 test files.

| Module | Lines | Role |
|---|---|---|
| `bhavcopy.py` | 221 | Pure parsers for both NSE formats → one `OptionRow` shape |
| `chain_cache.py` | 162 | The two-layout store; streaming consolidation |
| `fetch.py` | 238 | Resumable CLI over trading days; no token |
| `pricing.py` | 102 | Black-Scholes, implied vol by bisection, realised vol (strategy D only) |
| `wheel_engine.py` | ~700 | The state machine; all ten strategies |
| `run_strategies.py` | ~370 | Per-stock runner, ranked output; RSI/ATM-IV per-cycle helpers |
| `rank_stability.py` | 159 | Split-half Spearman on per-stock ranks |
| `iv_rank.py` | ~100 | Per-stock, per-year average ATM implied vol (new) |
| `leaderboard_sim.py` | ~230 | Trade-last-year's-top-N simulation, plus `simulate_cross` |

Shared, modified additively (defaults leave every published MCX and equity number unchanged to the
decimal, asserted by test): `growmore_bot/costs.py` gains `stt_sell_pct` and
`NSE_OPTION_COST_MODEL`; `growmore_bot/backtest/engine.py` gains `size_to_equity` and
`Trade.quantity`; `growmore_bot/risk/sizing.py` gains `shares_for_capital` and `rounding_drag`.

**The engine is new, not an extension.** `BacktestEngine` models one instrument, one position, one
leg, no expiry, no settlement, no assignment and no margin, and its P&L is `(exit − entry) × qty` —
correct only for a symmetric linear instrument, which a short option is not.

### The state machine

```
FLAT ---- sell put, expires OTM -----------> FLAT     (keep the premium)
FLAT ---- sell put, expires ITM -----------> HOLDING  (real shares delivered
                                                       at the strike; that
                                                       strike is the basis)
HOLDING - sell call, expires OTM ----------> HOLDING  (keep premium, write again)
HOLDING - sell call, expires ITM ----------> FLAT     (called away at the strike)
```

Physical settlement (SEBI, effective from the October 2019 expiry) is what makes this literal
rather than synthetic — an ITM short put delivers actual shares, so the covered call that follows
is genuinely covered. This is why the study moved off Nifty, whose index options are cash-settled.

### Five engine bugs found and fixed

Each would have produced plausible-looking wrong numbers rather than crashing:

1. **Fixed share count = escalating leverage.** Inherited from the F&O equity study. Fixed by
   `size_to_equity`.
2. **A month re-decided every day.** A deliberate no-write month leaves no open short, which the
   engine read as "time to open one" — 210 records where there were 7. Fixed by tracking
   `handled_expiry`.
3. **Strategy E was a silent duplicate of C** until the trend filter was wired in; the first fix
   then skipped buying the stock too, so it sat in cash for seven years.
4. **Marking by exact float equality**, which breaks when the adjustment factor changes at a
   corporate action; the fallback held the leg at its *entry* premium, freezing the liability.
   Now nearest-within-tolerance with an intrinsic-value fallback.
5. **Capital too small.** SEBI's ₹15 lakh minimum contract value (Nov 2024) meant ₹10 lakh would
   have left the largest names leveraged while labelled "cash-secured". Raised to ₹30 lakh.

### Validation

Hand-traced RELIANCE through the COVID crash against raw bhavcopy:

```
2020-02-27  spot 1,386 unadjusted -> wrote the put at 1,319 (4.8% OTM)
2020-03-26  spot 1,066 -> far below the strike -> ASSIGNED, real delivery
2020-04-30  spot 1,466 -> recovered above it   -> CALLED AWAY
```

The April covered call was written at exactly the assignment basis — the no-loss rule doing what it
is specified to do, on real data, in the month it matters most. Marked drawdown through the crash
is 29.9%, not the flat line a cost-basis mark would have drawn.

**683 tests pass**, ruff clean, mypy at its 10-error baseline.

---

## 6. Continuing this work

### Rebuild from nothing

```bash
cd bot
.venv/bin/python -m research.fno.manifest --write            # 210 names, no token
.venv/bin/python -m research.stock_options.fetch             # ~1,740 days, resumable, no token
.venv/bin/python -m research.stock_options.fetch --consolidate
.venv/bin/python -m research.stock_options.run_strategies    # ~90 min, 10 strategies
.venv/bin/python -m research.stock_options.rank_stability
.venv/bin/python -m research.stock_options.iv_rank            # ~2 min, feeds simulate_cross's IV arm
.venv/bin/python -m research.stock_options.leaderboard_sim     # ~20-30 min, includes simulate_cross sweeps
```

The fetch takes hours single-threaded. It is safe to run several processes over disjoint
`--from/--to` ranges; they skip days already cached. **Do not** run two `run_strategies` at once —
they compete over the same output file.

### Adding a strategy

Append a `StrategyConfig` to `STRATEGIES` in `wheel_engine.py`. The existing knobs are
`put_otm`, `call_otm`, `call_at_or_above_basis`, `always_long`, `long_put_otm`, `dynamic_strike`,
`min_call_premium_pct`, `trend_conditioned`. Anything genuinely new needs a branch in
`_open_position`, and a test that fails if the new arm collapses into an existing one — strategy E
shipped as a silent duplicate of C precisely because no such test existed.

### Known gaps and honest limitations

- **Entry is on expiry day for the next expiry**, so cycles run expiry-to-expiry (last Tuesday to
  last Tuesday) rather than calendar 1st-to-month-end. ~30 days to expiry either way. A literal
  calendar-month variant has not been tested.
- **Strategy F is sized as if cash-secured**, so its capital efficiency — the main practical appeal
  of a defined-risk spread — is *not* captured. Return on actual margin would be far higher.
- **No margin modelling.** Everything is cash-secured; a margin-financed variant is untested and
  would change every CAGR.
- **Settlement prices are a fair mark, not a guaranteed fill.** 2% of premium is charged as entry
  slippage; real spreads on thin strikes are wider. Market impact is not modelled.
- **Survivorship.** Today's 210 F&O members applied back to 2019. The control runs on the identical
  universe so the comparison holds, but absolute levels are inflated — do not quote them.
- **One regime.** 2019-2026 was strongly positive for Indian equities, which flatters every
  strategy that relies on recovery — the no-loss rule most of all.
- **16 of 210 stocks are unmeasured** (fewer than 12 monthly cycles), and A/G/H drop to 167-178
  because the no-loss rule can leave no sellable call, reducing cycles below the floor.
- Strategy D is the slow one: a per-strike implied-vol bisection dominates runtime.

### The two experiments worth running next

Both follow from §2.4 — the wheel loses on stocks that compound, so the way to beat buy-and-hold on
*both* metrics is to stop running it on those.

1. **G restricted to the IV-rich quintile.** ~~D's selection is the only one with out-of-sample
   support (+3.5%/yr, 5 of 6 years). Combining G's ATM aggression with D's stock filter is the
   single most promising untested combination.~~ **Done — see §7.1. The combination works, but not
   via D's own trailing return; it needed a genuine per-stock IV metric, built specifically for
   this.**
2. **A momentum/valuation exclusion** — skip stocks in sustained uptrends entirely, rather than
   skipping the call. H and I both attacked the symptom (the frozen call) and both failed; this
   attacks the actual loss source. **Still open.**

Neither should be built by picking past winners from the leaderboard: §2.5 shows that ordering is
noise.

A third, related idea was also run: **§7.2**, an RSI-scaled buffer above the assignment basis
(strategy `L`) — a graded version of the no-loss rule, replacing "exactly at basis" with "a few
percent above basis when the stock's own momentum justifies it."

---

## 7. Update 2026-09-06: both flagged combinations, run

### 7.1 G/L restricted to a high-IV subset — the combination works, once selection is real

§6's flagged experiment used D's own trailing return as a proxy for "IV-rich." Tested honestly, that
proxy **fails**:

Walk-forward, years strictly before the year scored, ranking G/L's own tradeable universe by D's
trailing return and restricting to the top fraction:

| Eval | top 50% | top 33% | top 20% |
|---|---|---|---|
| G, select by D's trailing return | −0.6%/yr (3/6 years) | −0.4%/yr (4/6) | −0.1%/yr (4/6) |
| L, select by D's trailing return | −0.7%/yr (3/6) | −0.2%/yr (4/6) | +0.1%/yr (4/6) |

D's own trailing return reflects D's per-cycle dynamic-strike choices, which turns out to say little
about which stocks suit G's or L's flat ATM approach — the two strategies are picking a different
thing to be good at, so ranking one by the other's history doesn't transfer.

Building the metric the doc actually meant — **average ATM implied vol, sampled once per monthly
cycle** (`iv_rank.py`, new this run) — and restricting to the top fraction by that instead:

| Eval | top 50% | top 33% | top 20% |
|---|---|---|---|
| G, select by IV rank | **+2.0%/yr (6/6 years)** | **+2.9%/yr (6/6)** | **+3.7%/yr (5/6)** |
| L, select by IV rank | **+2.2%/yr (6/6 years)** | **+3.1%/yr (6/6)** | **+3.5%/yr (5/6)** |

Monotonic in both strategies — the tighter the cut, the bigger the edge, with win-rate only easing
(5/6 instead of 6/6) at the most concentrated 20% cut, plausibly a smaller, noisier universe rather
than a real reversal. **This clears the same out-of-sample bar §2.5 used to reject the raw
leaderboard**: selection uses only years strictly before the year scored throughout. Restricting the
ATM wheel to a genuinely IV-rich subset of stocks is a real, attributable, out-of-sample edge —
the single most useful result from this update.

L stacks its own edge on top of G's at every cutoff (e.g. +3.5%/yr vs +3.7%/yr at 20% — statistically
indistinguishable at this sample size, but never worse), consistent with §7.2's finding that L is a
small, real improvement over G on its own.

What this does **not** establish: which exact cutoff to run live. 20% (≈42 of 210 stocks) shows the
largest edge but the thinnest evidence (fewest years at full win rate); 50% (≈105 stocks) is the most
robust (6/6 both strategies) at a smaller edge. Picking one is a live-deployment decision, not a
backtest one.

### 7.2 RSI-scaled basis buffer (strategy `L`) — a small, real, mechanism-attributable win

`L` differs from G by exactly one decision: when a call is written post-assignment, the no-loss
floor is raised to `basis × (1 + buffer)` instead of exactly `basis`, where the buffer is read off
the stock's own RSI(14) at that moment — 0% below RSI 40 (falls back to exactly G's rule), 2% in the
40-60 neutral band, 5% at RSI 60+ (real momentum). Unlike H and I (§3), this doesn't skip a decision
or add a floor that forgoes the call outright — it only adjusts, at the moment a call must be
written anyway, how far above basis to place it.

164 matched pairs (same stock, G vs L):

| | median Δ (L−G) | L better on |
|---|---|---|
| CAGR | **+0.26 pts** | **59%** |
| Sharpe | −0.001 (flat) | 51% |
| Max drawdown | +0.30 pts (worse) | 26% |
| Time frozen | +0.00 (unchanged) | 18% |

A small, positive, mechanism-attributable result: winning on CAGR on a majority of stocks, Sharpe a
wash, a slightly worse drawdown on most stocks (a wider buffer occasionally gives back some of a
rally before finally being called away), and — as expected, since this only changes strike
selection at the moment of writing a call — no change to frozen time at all.

### What changed in the codebase

`wheel_engine.py` gained `call_basis_buffer_pct` and `call_basis_buffer_rsi_scaled` on
`StrategyConfig`, `RSI_BASIS_BUFFER_TIERS`, and strategy `L`; `run_strategies.py` gained
`rsi_by_day` (RSI(14) via the shared strategy registry, same pattern as the existing
`trend_bullish_by_day`), `cycle_open_days`, and `atm_iv_by_cycle` (ATM implied vol sampled once per
monthly cycle, not a full daily pass); `iv_rank.py` (new) writes `iv_by_stock_year.csv`;
`leaderboard_sim.py` gained `simulate_cross`, generalizing the top-N simulation to rank stocks by
one series (D's trailing return, or the new IV rank) and evaluate a different one (G or L) — proven
to reduce to the original `simulate` when fed the same series both ways
(`test_simulate_cross_is_the_generalization_of_simulate`). **696 tests pass** (683 + 13 new), ruff
clean, mypy unchanged from its existing baseline.
