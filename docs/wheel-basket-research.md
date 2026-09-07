# The wheel basket, refined: method declared before the results

**Written 2026-09-07, before a single basket backtest was run.** That ordering is the point, and it
is the same discipline `docs/fno-universe-research.md` and `docs/stock-options-research.md` were
written under. Every variant, threshold, metric and accept/reject rule below was fixed while the
answer was still unknown, so nothing here can be a rationalisation of a number someone liked.
Results go in `docs/wheel-basket-results.md`, separately, afterwards.

Code: `bot/research/wheel_basket/`. **Nothing in this study writes to Neon**, and no Alembic
migration is part of it.

## 1. What is being fixed, and what is not

The live wheel basket (`bot/growmore_bot/wheel_basket/`) selects stocks on exactly one metric: the
cross-sectional ATM implied-vol percentile rank that cycle, restricted to the top 33%. That is not
an oversight — it is the only selection rule with an out-of-sample result behind it
(`docs/stock-options-results.md` §7.1: +2.9%/yr, 6 of 6 years, at the 33% cut), and
`scoring.py`'s docstring says in as many words that nothing else was invented because nothing else
had been tested.

Three refinements are requested, and this study tests them:

1. **Sector diversification** — the basket can currently put every rupee in one sector.
2. **Market regime** — nothing looks at whether the market is trending, ranging or falling.
3. **Per-stock technical condition** — RSI and MACD are computed and recorded, but touch only the
   covered-call basis buffer, never selection.

**A fourth was requested and is deliberately deferred: pending quarterly results.** No point-in-time
earnings calendar exists anywhere in this repo, and NSE publishes only forward-looking result dates,
so an earnings input could be built for live trading but could not be *validated* over 2019–2026.
Shipping an unvalidated selection input is the exact thing §1 of this document exists to prevent.
It is recorded in `docs/technical-debt.md` and revisited as its own study.

**What this study cannot fix.** Survivorship: the 210-name F&O universe is today's membership
applied backwards, exactly as `research/fno/manifest.py` documents. The mitigation is the same one —
the buy-and-hold control runs on the identical universe, so the bias hits both arms and the
*difference* stays meaningful even though neither *level* does.

## 2. The blocking gap: there is no wheel-basket backtest

This has to be said plainly, because it is most of the work. The wheel basket is a live/paper
decision loop that writes ORM rows against a real-time Dhan option chain. It has never been
backtested. The nearest thing, `research/stock_options/wheel_engine.run_wheel`, is **per-symbol**
with its own capital pool, and structurally cannot express the three things a basket is:

- one shared capital pool contended for by many symbols,
- rotation between symbols with hysteresis,
- any cross-sectional constraint, which is what a sector cap is.

So Phase 1 below builds a basket-level engine, and the refinements are variants on it. Every claim
in the results doc rests on that engine being right, which is why the parity test in §7 is
non-negotiable rather than nice to have.

## 3. Data, and its one gap

| Input | Source | Coverage |
|---|---|---|
| Option chains (strike, premium, settle, OI, volume, lot size, underlying) | `research/.cache/stock_options/symbols/` — real NSE F&O bhavcopy, **not synthesized** | 2019-09-03 → 2026-09-04, 210 symbols |
| Equity OHLCV | `research/.cache/fno_bars/` | up to 15 years, 210 symbols |
| Sector labels | `research/fno/universe.csv` (`nse_industry`, `is_defence`) | all 210 |
| Implied vol | computed by bisection, `research/stock_options/pricing.implied_vol` | there is no IV column in bhavcopy |
| NIFTY 50 + INDIA VIX daily | Dhan `IDX_I` — **security_id 13 and 21**, confirmed against the live scrip master 2026-09-07 | **not yet fetched — see below** |

Chain strikes are unadjusted; cached cash bars are corporate-action adjusted. Every run converts the
chain into adjusted space with `run_strategies.adjustment_factors` / `to_adjusted_space` first. This
is not optional and is the trap both `fetch.py` and `run_strategies.py` warn about in their
docstrings.

**The index gap, stated up front rather than discovered later.** The regime work in Phases 3 needs
NIFTY 50 and INDIA VIX daily history. The security IDs are confirmed, and `DhanClient` needs no
change (it reads `exchange_segment`/`instrument_type` off a duck-typed instrument). But the Dhan
access token in `.env.local` is a 24-hour token that expired 2026-09-06 08:17 UTC, so coverage back
to 2017 is **confirmed-reachable in principle and unverified in fact**. The index series is
therefore an *injected dependency* throughout: every module and test below works without it, and
only the final regime run needs it. If Dhan turns out not to serve that depth, the declared
fallbacks, in order, are (a) NSE's own historical index CSVs, (b) for VIX only, the cross-sectional
median ATM IV already computable from the chain cache. Whichever is used will be named in the
results doc.

**Warm-up window.** The backtest scores 2019-09 onward, and the regime classifier needs a 200-day
SMA and a 504-day trailing VIX percentile before its first label. Index history is therefore
fetched from **2017-01-01**, and no regime label is emitted before its full warm-up is available.

## 4. The trial budget: 12 variants, not 432

| Stage | Configs | Each differs from the previous stage's winner by |
|---|---|---|
| A — baselines | 2: `B0` (live logic replayed), `BH` (buy-and-hold the universe) | benchmarks, not trials |
| B — sector | 3: round-robin; round-robin capped 1/sector; capped 3/sector | the sector constraint |
| C — regime | 3: `R1` gate, `R2` strike aggressiveness, `R3` sizing | one regime *use* |
| D — per-stock | 3: `T1` headwind, `T2` support strike, `T3` relative strength | one stock signal |
| E — combination | ≤3 | only mechanisms that won alone |

**12 variants + 2 benchmarks = 14 runs. Sequential, not cross-product.** The cross-product is 48
configs; add the three `top_iv_frac` cuts and three hysteresis values and it is 432; sweep the
regime thresholds inside each and it is thousands. At that size the deflated-Sharpe bar rises faster
than any edge here could clear it, and the output is a best-of-N artefact — the error recorded in
`docs/crosstrend-results.md` and §2.5 of the options results. The stance in `run_all.py` and
`research/fno/configs.py` is that the grid should shrink, not grow.

**Thresholds are declared in §5 and §6, never swept.** A swept threshold is a hidden trial.

**Two knobs stay frozen at today's live values**: `top_iv_frac = 0.33` and
`rotation_hysteresis_pct = 0.10`. §7.1 already spent its trials on the IV cut and concluded that
choosing between 20/33/50% is a live-deployment decision, not a backtest one. Re-sweeping it here
would spend this budget re-answering a settled question and contaminate every comparison above it.

**Honest caveat.** Sequential greedy selection inflates the effective trial count above 12, because
stages C, D and E all build on B's winner. `research/validation/effective_trials.py` estimates the
correlation-adjusted count and the verdict is quoted as a deflated Sharpe against *that*, never
against a flattering 12.

## 5. The regime classifier, fully specified

A pure function of **trailing** NIFTY 50 and INDIA VIX data only. No bar at or after `t` may be
read, and a unit test asserts that appending future bars cannot change an already-emitted label.

Inputs, at conventional parameters chosen because they are conventional, not because they were
tuned: `SMA50`, `SMA200`, MACD(12, 26, 9) on the index close, and INDIA VIX's percentile within its
own trailing 504 trading days.

Evaluated in this order; first match wins:

| State | Condition |
|---|---|
| `high_vol` | VIX trailing percentile ≥ 0.80 |
| `bull` | close > SMA200 **and** SMA50 > SMA200 **and** MACD histogram > 0 |
| `bearish` | close < SMA200 **and** SMA50 < SMA200 |
| `consolidating` | anything else |

`high_vol` is checked first because it is a statement about option pricing, which outranks trend for
a premium seller.

### The three uses

**R1 — gate.** No *new* put entries while the regime is `bearish`. Open positions run to their own
terms; assignment and covered calls are unaffected. **`high_vol` is deliberately NOT gated**, and
this is a prediction as much as a design choice: high IV is precisely when a premium seller earns
most, so gating it should hurt. Gating both would bundle the two and make neither attributable.

**R2 — strike aggressiveness.** Capital stays deployed; only the target OTM distance moves:

| State | put OTM | call OTM |
|---|---|---|
| `bull` | 0.00 | 0.00 |
| `consolidating` | 0.00 | 0.00 |
| `bearish` | 0.05 | 0.00 |
| `high_vol` | 0.05 | 0.00 |

**R3 — sizing.** Strikes unchanged; the fraction of the pool deployed moves: `bull` 1.00,
`consolidating` 0.75, `high_vol` 0.60, `bearish` 0.40.

## 6. Sector and per-stock rules, fully specified

**Sector round-robin.** Order eligible candidates by IV percentile within each NSE `nse_industry`,
then take the best from each sector, then the second from each, and so on until capital is
exhausted. Sector is NSE's own 18-bucket `Industry` field.

**Defence stays a non-exclusive overlay, never a 19th bucket.** BEL, HAL, BDL, MAZDOCK and COCHINSHIP
are Capital Goods; BHARATFORG is Automobile; SOLARINDS is Chemicals. Making Defence exclusive would
pull those seven out of the sectors they genuinely belong to and distort every count — the argument
`research/fno/sectors.py` already makes. Defence exposure is *reported* as a metric, never
constrained.

Note the shape of the constraint before it runs: Financial Services is **55 of the 210** names, so
round-robin binds hardest there, and a 1-per-sector cap is a severe restriction rather than a mild
one. Every results table reports **max sector share of deployed capital** next to return, because
that number is the thing actually being bought.

**Per-stock signals**, all applied at *stock selection* — deliberately a different decision point
from strategy `I`, which gated whether to *write the call* and is recorded in
`docs/stock-options-results.md` §3 as one of the two enhancements that **failed**:

- **T1 — headwind filter.** Drop a candidate from put entry when MACD(5, 13, 5) is bearish **and**
  its close is below its own SMA200. Both conditions, not either: a single weak signal on 210 names
  is mostly noise.
- **T2 — support-aware strikes.** Place the short put at or below the lowest low of the trailing 60
  trading days, instead of flat ATM.
- **T3 — relative strength.** 60-day stock return minus 60-day NIFTY 50 return, used only as the
  ordering tiebreak *within* each sector's round-robin queue. It changes ordering, never eligibility.

## 7. What is measured, and what counts as a win

Per config: CAGR, Sharpe, max drawdown, win rate, profit factor (`None` meaning infinite, the
convention throughout this repo), plus the metrics `wheel_engine` established as first-class because
Sharpe hides them — **`time_frozen_pct`** and **`time_underwater_pct`** — plus **max sector share of
deployed capital**, defence exposure, cycle count, assignment count and per-year return.

Positions are **marked to market daily**, not at cost basis. Marking an underwater assigned holding
at its basis draws a smooth equity curve that is a lie; this is the rule `wheel_engine`'s docstring
states and it carries over unchanged.

Costs: `NSE_OPTION_COST_MODEL` and `NSE_EQUITY_DELIVERY_COST_MODEL` from `growmore_bot/costs.py`,
plus `PREMIUM_SLIPPAGE_PCT = 0.02` — 2% of premium, because options slip on premium, not in ticks.

**A variant is declared a win only if all three hold:**

1. It beats `B0` on CAGR without worsening max drawdown by more than 2 points, **or** it improves
   max drawdown by more than 3 points at a cost of no more than 0.5 CAGR points.
2. It wins in at least 4 of the 7 years.
3. The mechanism is attributable: the matched-pair median against `B0` (same stock, same cycle) is
   positive — the comparison §7.2 used for L vs G. An aggregate CAGR difference across a *changing*
   basket is not evidence about a mechanism.

Anything else is recorded as a failure in the results doc and not carried into stage E. Failures get
written up; that is what §3 of the options results doc is for.

## 8. Predictions, recorded now

Stated so they can be marked right or wrong later, the way `docs/fno-universe-research.md` recorded
its prediction and was scored "half right".

1. **Sector round-robin lowers return slightly and lowers drawdown more.** Diversification is a
   constraint, not alpha. If it *raises* return materially, the correct response is suspicion — most
   likely it dodged Financial Services during a specific bad stretch, which is a story about
   2019–2026 and not a mechanism.
2. **R1 (gate on bearish) helps drawdown and costs CAGR.** A premium seller who stands aside earns
   nothing while standing aside.
3. **R3 (sizing) beats R1**, because it modulates rather than eliminates, and the wheel's income is
   roughly linear in deployed capital while its tail risk is not.
4. **T1 fails.** Per-stock trend filtering already failed once as strategy `I`. Moving it to the
   selection decision is a genuinely different question, but the prior is unfavourable, and IV rank
   and trend are not independent — high IV frequently *is* a falling stock.
5. **The combination in stage E underperforms the sum of its parts**, because these mechanisms
   overlap: a bearish market, a bearish stock and a high-IV stock are substantially the same event.
