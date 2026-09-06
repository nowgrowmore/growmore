# Monthly stock-option strategies: method, declared before the results

**Written 2026-09-06, while the option chains were still downloading.** That ordering is the point.
Every metric, guardrail and decision rule below was fixed while the answer was unknown, so nothing
here can be a rationalisation of a number someone liked. Results go in
`docs/stock-options-results.md`.

Code: `bot/research/stock_options/`. Nothing in this study writes to Neon.

## Why stock options and not Nifty

The request began as a weekly Nifty options wheel: sell puts, never close them, and after
assignment write covered calls only at or above the assignment price so no loss is ever realised.

**Nifty index options are European and cash-settled.** An ITM short put pays cash and leaves you
holding nothing, so there is no assignment and nothing to write a covered call against. The wheel
would have had to be synthesised with futures, and the "covered" call would have been covered only
by a proxy.

**Indian stock options are physically settled** (SEBI, fully effective from the October 2019
expiry). An ITM short put delivers real shares at the strike. So on stock options the strategy is
literal — and 210 underlyings give a cross-section, where a single Nifty path gives one history.

## The data

NSE's public F&O bhavcopy, free and without a token — Dhan cannot serve this at all, having no
history for expired contracts. Two formats normalised to one row shape: UDiFF from 2024-07,
legacy before. Carries strike, option type, expiry, OHLC, **settlement price**, open interest,
volume, underlying and lot size.

Liquidity is not a concern here, which is the point of this universe: on 2026-09-04 all 210 stocks
had live monthly options, median 11,782 contracts traded in the near month, one stock under 1,000
and none at zero. This is the opposite of the small-cap study's unvalidated-fills risk.

**Expiry selection is not available and that is a data fact, not a simplification.** 98.9% of
stock-option volume sits in the near month, 1.0% in the next, and the third month trades in only
43 of 210 names. Selling a far-month option would be modelling fills that do not exist. The strike
axis does have room — median 16 traded put strikes per stock per month, median 8 inside the 1-15%
OTM band, all 210 stocks covered — so that is where the one strategy allowed to choose, chooses.

### Three data traps, all found by checking rather than by luck

1. **Adjusted vs unadjusted.** The cached cash series is corporate-action adjusted; historical
   option strikes are not. RELIANCE closes at 589.54 adjusted on 2019-10-03 while its puts sat
   between 900 and 1600 — a 2.14× gap from the 2024 bonus. Filtering one against the other silently
   discarded every row on the first run. The underlying now comes from an unadjusted source in both
   eras, and prices are converted to a single continuous space before the engine sees them.
2. **Lot size.** Absent from legacy files. The obvious turnover identity fails on option rows
   because NSE reports option turnover on the *underlying's* notional, not the premium — validated
   against UDiFF's known values, that route reproduced 2 of 210. Applied to the futures rows in the
   same file it reproduced 54 exactly and the rest within ~1%. That residue is immaterial: positions
   are one lot against `strike × lot` of capital, so the lot size divides out of every return.
3. **Cost base.** Option STT is 0.1% of *premium*, sell side only, and `leg_cost` takes one turnover
   number with nothing stopping a caller passing `strike × lot` instead — a ~60× overstatement on a
   5% OTM monthly. Assignment is separately an equity *delivery* trade and is taxed as one, an order
   of magnitude dearer than the option leg it came from.

## The six strategies

Each is a declared hypothesis, not a parameter sweep. Strike is fixed at **5% OTM** for all but D,
which is the only one permitted to choose — that is what keeps the matched pairs attributable.

| | Strategy | The idea |
|---|---|---|
| **A** | Wheel, constrained | Sell 5% OTM cash-secured put monthly; if assigned, take delivery; write covered calls **only at strikes ≥ the assignment basis**; repeat when called away. |
| **B** | Wheel, unconstrained | Identical but calls may be written at the best strike regardless of basis. The matched pair that isolates what the no-loss rule is worth. |
| **C** | Buy-write | Own the stock throughout, sell a 5% OTM call monthly. |
| **D** | IV-richness, dynamic strike | Write where implied vol stands furthest above the stock's own realised vol, subject to a 0.35 delta cap. |
| **E** | Trend-conditioned calls | Write calls only when the trend rule says the stock is not running. |
| **F** | Put credit spread | Sell the 5% OTM put and buy the 10% OTM put. Same short strike as A; the only difference is the long put underneath. |

**Control, mandatory: buy-and-hold the stock**, same window, same capital. It beat the trading
system on five of eight MCX contracts and on 95% of 193 F&O stocks. It is a column in every table.

F is declared **now**, not conditionally after seeing whether the wheel disappoints — running it
only on a disappointing result would be post-hoc selection, the move that turns a grid into a
search. Physical settlement makes it messier than the textbook: finishing between the strikes
delivers shares from the short put while the long put expires worthless, so only below the long
strike does the spread cap out. The engine handles all three regions explicitly.

## The rules, fixed in advance

1. **Per-stock results are the deliverable**, ranked by CAGR and by Sharpe, each with the stock's
   own buy-and-hold beside it.
2. **The leaderboard ships with its own guardrail.** 210 stocks × 6 strategies ranked means the top
   is partly luck, on ~84 monthly cycles per stock. Split-half rank stability decides whether the
   ordering carries information; near-zero Spearman means top-picking will fail live.
3. **Marked to market daily, both legs.** The no-loss rule prevents *realising* a loss, not
   incurring one: assigned at 1,400 with the stock at 1,000, every strike at or above basis is 40%
   away and pays nothing, so the position sits frozen and underwater. Marking at cost basis would
   draw a smooth curve that is a lie. `time_underwater_pct` and `time_frozen_pct` are first-class
   metrics because that is where this strategy's risk lives and Sharpe hides it.
4. **Position size is re-derived from current equity at each entry.** A fixed lot count over seven
   years of compounding is escalating leverage — the bug that invalidated the first run of the F&O
   equity study, and worse here because a cash-secured put must actually hold `strike × lot`.
5. **A strike must have actually printed to be sellable.** Bhavcopy lists strikes that never traded.
6. **Guardrails flag, never drop**: under 12 monthly cycles a stock is *unmeasured*, not weak.
7. **Nothing is swept and nothing is tuned.** The grid shrinks, it does not grow.

## What will be wrong regardless

- **Settlement prices are a fair mark, not a guaranteed fill.** 2% of premium is charged as
  slippage on entry; real spreads on a thin strike are wider.
- **Market impact is not modelled.** Small at one lot in an F&O-eligible name; not zero.
- **Survivorship.** Today's 210 F&O members applied back to 2019. The control runs on the identical
  universe so the bias hits both arms, but absolute levels are inflated — do not quote them.
- **STT on option sales rose from 0.0625% to 0.1% in October 2024.** Earlier cycles were cheaper;
  the higher rate is charged throughout, which is conservative for the early period.

## The prediction, recorded before the results

1. **The wheel and buy-write beat buy-and-hold on Sharpe and lose on CAGR**, on most stocks.
   Selling calls caps exactly the upside that produced the equity study's +15.5% median CAGR.
2. **The no-loss constraint costs money.** Expect A to show a *better win rate* and *worse* CAGR and
   time-underwater than B — the shape of a rule that optimises the trade log rather than the account.
3. **The CAGR leaderboard will be substantially unstable; Sharpe somewhat less so.** Expect
   split-half Spearman near zero for A/B/C. This matters more than any single ranking.
4. **D is the only one with a real chance of a stable ranking**, because IV richness is a persistent
   stock characteristic rather than a realised outcome.
5. **Assignment rates will be far higher than intuition suggests** — a 5% OTM monthly put on a
   35%-vol stock is roughly a one-standard-deviation move away.
6. **F wins on Sharpe and on return-on-capital and loses on raw CAGR.** The long put is a cost paid
   every month; what it buys is the left tail, which is where A actually dies.

What would overturn this: a constrained wheel beating buy-and-hold on both CAGR and drawdown, or
positive split-half rank correlation on A/B/C.

## Reproducing

```
cd bot
.venv/bin/python -m research.stock_options.fetch          # ~1,740 days, resumable, no token
.venv/bin/python -m research.stock_options.fetch --consolidate
.venv/bin/python -m research.stock_options.run_strategies
.venv/bin/python -m research.stock_options.rank_stability
```
