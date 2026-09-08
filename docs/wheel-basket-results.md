# Wheel-basket refinement: results

Method was declared first, in `docs/wheel-basket-research.md`, before any of this was run. Nothing
below has been added to that document after the fact; where a prediction was wrong, it is marked
wrong here rather than quietly amended there.

Code: `bot/research/wheel_basket/`. Data: the real NSE F&O bhavcopy chains already cached
(2019-09-03 → 2026-09-04), 199 of the 210 F&O names with enough option history, 11,353
symbol-cycles. **Nothing in this study wrote to Neon.** Raw table:
`bot/research/.output/wheel_basket/configs.csv`.

## 1. Headline: the wheel basket loses to owning the same stocks

| config | CAGR | Sharpe | max DD | mean sector share | frozen capital | wheels left open |
|---|---|---|---|---|---|---|
| **BH — buy and hold the universe** | **15.24%** | **1.07** | **20.6%** | — | — | — |
| B0 — today's live logic | 13.64% | 0.97 | 20.0% | 0.41 | 43.7% | 11 |
| B1 — fixed 10 slots (control) | 10.55% | 0.82 | 25.0% | 0.46 | 59.0% | 8 |
| S1 — sector round-robin | 21.34% | 0.71 | 28.3% | 0.39 | 49.3% | 9 |
| S2 — cap 1 per sector | 20.41% | 0.66 | 27.8% | 0.35 | 54.6% | 9 |
| S3 — cap 3 per sector | 21.34% | 0.71 | 28.3% | 0.39 | 49.3% | 9 |
| T1 — per-stock headwind filter | 10.13% | 0.80 | 25.0% | 0.45 | 59.6% | 8 |
| T2 — support-aware put strikes | 10.85% | 0.69 | 24.0% | 0.49 | 52.5% | 7 |

**The live configuration underperforms buying and holding the identical universe** — 13.64% against
15.24%, at a slightly worse Sharpe. That is the same verdict, on the same kind of control, that
`docs/smallcap-momentum-backtest-results.md` reached about its own strategy, and it is the first
thing to say because every refinement below is a refinement of something that is not yet beating its
benchmark.

The survivorship caveat from `research/fno/manifest.py` applies and cuts both ways: the F&O universe
is today's membership applied backwards, so *both* arms are flattered equally and the DIFFERENCE
between them remains meaningful even though neither LEVEL does.

## 2. Sector diversification: no, and the reason is more useful than the answer

S1 and S3 look like large wins on CAGR (21.3% against the 10.5% control). They are not, on three
independent grounds:

**Sharpe falls, from 0.82 to 0.71**, while max drawdown rises from 25.0% to 28.3%. The extra return
is bought with more risk than it pays for.

**The per-year record is a coin flip.** Against the control, year by year:

| | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|
| B1 control | 10.5 | −4.8 | 7.5 | 2.0 | 9.3 | 3.6 | 29.3 | 16.7 |
| S1 round-robin | 3.0 | −9.5 | 12.3 | 5.6 | 7.4 | 39.3 | 24.9 | **83.2** |
| S2 cap 1/sector | 2.4 | −12.3 | 9.1 | 6.0 | 6.6 | 45.6 | 21.5 | **84.0** |

S1 beats the control in **4 of 8 years**; S2 in **4 of 8**. The declared accept rule (§7 of the
research doc) required wins in at least 4 of 7 years *as well as* an attributable mechanism. Four out
of eight is not evidence of anything.

**And almost the entire edge is one partial year.** 2026 covers January to September only, and it
carries an 83% return against the control's 16.7%. Strip 2026 and the advantage largely disappears.

**Verdict: sector diversification is REJECTED on this evidence.** Prediction 1 said a real
diversification effect should lower return slightly and lower drawdown more, and that a large return
gain should be treated as suspicious rather than welcome. It was a large return gain, it was
suspicious, and it did not survive.

### Why it could not have worked: the book is stuck

The mechanism finding is more valuable than the verdict. **Between 44% and 60% of deployed capital
sits below its assignment basis at any moment** (`time_frozen_pct`, capital-weighted). A wheel that
has been assigned and has fallen cannot write a call above its basis, so it cannot be exited without
realising the loss the strategy exists to avoid — and it is therefore still there next cycle, and the
cycle after.

Sector diversification is a rule about *what to buy next*. It has almost nothing to act on when most
of the book cannot be sold: over seven years and 82 cycles the basket traded only 40–55 distinct
symbols out of 199, and mean sector share moved only from 0.46 to 0.39 (round-robin) or 0.35 (a hard
one-name-per-sector cap). **You cannot diversify a portfolio you cannot exit.**

This also explains why S1 and S3 are numerically IDENTICAL: with ten slots spread across roughly
fifteen sectors, a three-per-sector cap never once binds.

## 3. Per-stock technicals: both rejected

- **T1, the headwind filter** (drop candidates whose MACD is bearish *and* which sit below their own
  SMA200): 10.13% against the control's 10.55%, beating it in **3 of 8 years**. Prediction 4 said
  this would fail, on the grounds that strategy `I` had already failed and that IV rank and weak
  trend are not independent — a high-IV stock frequently *is* a falling stock. **Prediction correct.**
- **T2, support-aware strikes** (short put placed at or below the trailing 60-day low): 10.85%, but
  Sharpe 0.69 and wins in **2 of 8 years**. Its one real effect is on frozen capital, which drops to
  52.5% from 59.0% — a deeper strike is assigned less often. That is a genuine mechanism and the only
  thing in this study that moved the frozen number in the right direction, but it does not pay for
  itself in return.

## 4. Not run: the market-regime stage

R1/R2/R3 and T3 need NIFTY 50 and INDIA VIX daily history. The security IDs are confirmed against
the live Dhan scrip master (**NIFTY 50 = 13, INDIA VIX = 21**, segment `IDX_I`) and `DhanClient`
needs no change to fetch them — but the access token in `.env.local` is a 24-hour token that expired
2026-09-06 08:17 UTC, so the data could not be fetched.

They are reported as NOT RUN rather than as null results. With no regime labels every regime variant
behaves exactly like its baseline by design, so a null result would be indistinguishable from "the
mechanism does nothing" — which is precisely the question being asked.

**4 of the 12 declared variants therefore remain unrun.** No deflated-Sharpe adjustment is quoted,
because nothing survived its accept rule; a DSR correction can only make a surviving result weaker,
never rescue a rejected one.

## 5. What was wrong with the engine before these numbers meant anything

Recorded because each was found by a test or a contradiction in the output, and each changed the
answer materially:

1. **A live over-commitment defect** (§6 below) — the most important finding in this document.
2. **Deployment confounded every variant.** Sizing each slot as `budget ÷ eligible candidates` meant
   any rule that shrank the candidate list enlarged every surviving position: measured deployment
   moved from 0.71 to 1.05 between configs that were supposed to differ only in *which* stocks they
   chose, so a sector cap was booking a leverage gain as a diversification gain. Fixed by the
   `B1-fixed-slots` control — ten equal-weight slots for every config.
3. **Carryover fill inverted diversification.** Sizing slot *i* as `free ÷ remaining` grows each
   successive slot as earlier candidates underspend, handing the END of the queue the most capital —
   and round-robin deliberately puts the crowded sector's second and third names at the end. The two
   together *concentrated* the book: mean sector share 0.52 against 0.29 for no round-robin at all.
   Fixed by redistributing leftovers in even passes.
4. **Concentration was measured on one day.** A daily maximum is dominated by sparse days: whenever
   exactly one position is live its sector share is trivially 100%, so every config scored ~0.95.
   Replaced with a capital-weighted mean across every open day.
5. **Frozen time saturated.** Flagging the whole book frozen if *any* position was underwater read
   96–99% for every config. Replaced with capital-weighted frozen fraction.
6. **A completed wheel left a zombie position** that was closed again on a later cycle, booking a
   trade worth exactly zero — neither win nor loss — which drove profit factor to infinity against a
   78% win rate.
7. **A corporate-action factor turned 83 strikes into 888.** The per-day adjustment factor carries
   float noise (RELIANCE: 30 distinct factors across 30 days, all ~0.44967), which destroys strike
   identity and inflated one symbol's matrices from 1.6 MB to 24 MB. Fixed with one factor per cycle.

**Win rate and profit factor are not reported above, and that is deliberate.** A wheel only
*completes* by being called away, which happens above the assignment basis and is therefore always
profitable — so win rate over completed wheels is ~100% by construction and measures nothing. The
losses live in wheels that never complete. `unfinished_positions` and `time_frozen_pct` are reported
in their place.

## 6. The finding that matters most, and it is not a backtest result

**The live wheel-basket engine would commit a median ₹2.47 crore against its configured ₹1 crore.**

`WheelBasketEngine._fill_empty_capital` divides the pool evenly across *every* eligible candidate
(median 43 names, so ~₹2.33 lakh each) and then sizes with `lots = max(1, capital_per_slot // (strike
* lot_size))`. A typical F&O lot needs ₹7 lakh or more of cash to secure, so **only 4.7% of
candidates are genuinely affordable at that slot size** — and `max(1, ...)` opens the other 95%
anyway, at one lot each.

Measured across the 82 cycles in the option history, cash needed to open one lot on every top-33%
name: **median ₹2.47 crore, range ₹0.92–6.21 crore**, against ₹1 crore configured. A median 2.5x
over-commitment, peaking above 6x.

This costs nothing today — the config is `enabled=false`, `mode=paper`, and the capital is virtual,
so the over-commitment is invisible. It would misprice every position on the first live cycle.
Tracked in `docs/pending-actions.md`.

## 7. Predictions, scored

| # | Prediction | Outcome |
|---|---|---|
| 1 | Sector round-robin lowers return slightly, lowers drawdown more; a large return gain should be treated as suspicious | **Half right.** The direction was wrong — it raised return — but the instruction to be suspicious of that was right, and the gain did not survive. |
| 2 | R1 (gate on bearish) helps drawdown, costs CAGR | **Not run** |
| 3 | R3 (sizing) beats R1 | **Not run** |
| 4 | T1 fails | **Correct.** 3 of 8 years. |
| 5 | Stage E combination underperforms the sum of its parts | **Not reached** — nothing won alone, so there was nothing to combine. |

## 8. What would actually be worth trying next

Ranked by what this study found, not by what was originally planned:

1. **Fix the live sizing defect** (§6). Nothing else matters until the engine can size a position.
2. **Attack frozen capital, not stock selection.** 44–60% of the book being stuck is the dominant
   fact, it is what caps the strategy below buy-and-hold, and T2 is the only lever tested that moved
   it. A study aimed squarely at exit rules for underwater assignments — rolling down, accepting a
   bounded realised loss, time-limiting a frozen holding — addresses the actual constraint. Sector
   and regime rules are refinements to an allocation decision that the book is mostly too illiquid to
   make.
3. **Then, and only then, the regime stage**, once the Dhan token is refreshed.
