# Three configs, 193 F&O stocks, 15 years: buy-and-hold wins on 95% of them

**Run 2026-09-05.** `python -m research.fno.run_configs` and
`python -m research.fno.walk_forward_run`, over 210 NSE F&O underlyings fetched from Dhan
(2011-09-05 → 2026-09-04, full OHLCV, 3,719 daily bars for a name listed the whole window; 193 of
the 210 clear the 1,260-bar inclusion gate). Real NSE cash-delivery costs charged per leg, ₹5,00,000
of capital per stock, positions sized to current equity, long-only. Method fixed in advance in
`docs/fno-universe-research.md`. Nothing was published to Neon.

## The headline

Each config was scored against **each stock's own buy-and-hold** over identical bars — 193 matched
pairs, not a leaderboard. On risk-adjusted return the configs lose on 95–99% of stocks.

| Config | Beats own B&H (Sharpe) | Median ΔSharpe | Median ΔCAGR | Beats own B&H (drawdown) | Median ΔDD |
|---|---|---|---|---|---|
| MACD (5,13,5) + 2×ATR stop, 3×ATR trail | **1.0%** | −0.683 | −19.4 pp | 36.3% | −6.06 pp |
| Ensemble ≥3 + same ATR block | **5.2%** | −0.557 | −16.6 pp | 51.8% | +1.13 pp |
| vol90 ensemble | **4.7%** | −0.571 | −16.7 pp | **58.0%** | **+3.65 pp** |

*ΔDD positive = smaller drawdown than holding. Sign-test p < 0.0001 on every Sharpe and CAGR row;
p = 0.031 on vol90's drawdown row, 0.67 on the ensemble's.*

In levels, across the 193:

| | Median CAGR | Median Sharpe | Median max DD | Median round trips | Costs, % of capital |
|---|---|---|---|---|---|
| MACD (5,13,5) | −4.6% | −0.05 | 70.1% | 288 | 44% |
| Ensemble ≥3 | −0.2% | 0.11 | 59.3% | 165 | 33% |
| vol90 ensemble | −0.3% | 0.08 | 56.7% | 146 | 30% |
| **Buy and hold** | **+15.5%** | **+0.61** | 59.6% | 0 | 0.1% |

**The trade on offer is roughly 16 points of annual CAGR for 3 points of drawdown.** That is not a
risk/return trade-off worth making, and for two of the three configs there is no drawdown benefit
at all.

## What I expected, and where I was wrong

`docs/fno-universe-research.md` predicted, before the data was fetched, that these configs would
"lose to buy-and-hold on return and beat it on drawdown." Half right. They lose on return, decisively
and everywhere. **They do not meaningfully win on drawdown** — the MACD config's drawdowns are
*worse* than holding (p = 0.0002), the ensemble's are a coin flip, and only vol90 achieves a
detectable improvement, of 3.65 percentage points at p = 0.031.

That is a worse result than predicted, and a more useful one: the usual defence of a trend overlay
— "it gives up return but buys you a calmer ride" — is not available here. It gives up the return
and mostly does not buy the calmer ride.

## The mechanism: these rules only help where holding lost money

Splitting the 193 by how buy-and-hold itself did:

| Buy-and-hold's own CAGR | Stocks | vol90 beats it | Median ΔSharpe |
|---|---|---|---|
| **Lost money (<0%)** | 8 | **38%** | −0.18 |
| 0–10% | 44 | 5% | −0.55 |
| 10–20% | 79 | 3% | −0.56 |
| 20–30% | 37 | 3% | −0.60 |
| 30%+ | 25 | 4% | −0.67 |

Monotonic, and the whole story. A long-only trend filter is **insurance against a stock that goes
nowhere**, and insurance costs money when the stock goes up. The eight stocks where holding lost
money are the only place the configs are competitive — and even there they win only 38% of the time,
so they are not good insurance either.

This also explains the leaderboard. Sorting all 193 by vol90's ΔSharpe puts PSU banks and
sideways-for-a-decade names on top — PNB (holding: −3.0% CAGR), Bank of India (−5.1%), Bandhan
(−11.6%). The configs are not finding trends; they are avoiding stocks that did nothing.

## Out of sample, the same answer to two decimal places

Everything above is in-sample over the full window. The walk-forward harness re-runs each config as
a **fixed variant with no re-selection** — Phase 1's conclusion was to stop selecting, not to
walk-forward the selection — scoring only bars that no fitting step ever touched. Train 504 / test
126 / step 126, grid hash `91ccf76aa776`, a median of **25 folds per stock**, 4,203 folds and 16,812
out-of-sample backtests in total.

| Config | Beats own B&H (Sharpe) | Median ΔSharpe | IQR | Sign-test p |
|---|---|---|---|---|
| MACD (5,13,5) | 3.1% | −0.703 | [−0.88, −0.50] | <0.0001 |
| Ensemble ≥3 | 3.1% | −0.546 | [−0.79, −0.36] | <0.0001 |
| vol90 ensemble | 3.1% | −0.606 | [−0.81, −0.40] | <0.0001 |

Against the in-sample medians of −0.683 / −0.557 / −0.571, this is agreement to within 0.03 Sharpe.
That is expected rather than lucky — nothing was fitted, so there was no overfit for the
out-of-sample test to expose — but it does close off the usual escape route. This is not a result
that survives only in-sample, and it is not a result that a different window would rescue: it
reproduces across 4,203 independent six-month windows spanning fifteen years and four regimes.

## Sector: no sector where the edge exists

The sector cut was declared as a robustness check, not a filter, and it answers cleanly. vol90's
win rate against buy-and-hold, by NSE macro sector:

| Sector | Wins | Sector | Wins |
|---|---|---|---|
| Financial Services | 6/49 | Power | 1/8 |
| Capital Goods | 1/20 | Realty | 0/6 |
| Healthcare | 0/15 | Consumer Services | 0/6 |
| Automobile & Components | 0/14 | Chemicals | 0/5 |
| Fast Moving Consumer Goods | 0/14 | Services | 0/4 |
| Information Technology | 0/12 | Construction Materials | 0/4 |
| Metals & Mining | 1/10 | Telecommunication | 0/3 |
| Consumer Durables | 0/10 | Construction | 0/3 |
| Oil Gas & Consumable Fuels | 0/9 | Textiles | 0/1 |
| | | *(overlay)* Defence | 1/7 |

**Fourteen of eighteen sectors: zero wins.** The only sector above 10% is Financial Services, which
is where the sideways PSU banks live — the same mechanism as above, not a sector effect. There is no
corner of this universe where the edge is hiding.

## Costs are large, and they are not the explanation

STT is charged at 0.1% on **both** legs of a cash-delivery trade, against MCX's 0.01% on the sell
alone. At the configs' turnover that is 30–44% of capital over fifteen years, or roughly 2–3% a
year. It is the single biggest line item and it is why the lowest-turnover config (vol90, 146 round
trips) is the best of the three.

But costs are **not** what decides this. Buy-and-hold beats these configs by ~16 points of CAGR a
year; the entire cost load is 2–3 points. Even charged nothing at all, the configs would still lose
decisively. The problem is the rules, not the tax.

## What would have made this wrong, and didn't

- **Corporate actions.** Splits and bonuses are adjusted in Dhan's series (IRCTC's 2021 1:5 split is
  backed out), but **demergers are not** — Adani Enterprises shows a −83% day in 2015 and Motherson
  four such days. Excluding all 11 flagged names changes nothing: vol90's Sharpe win rate moves
  4.7% → 3.8%, its median ΔDD +3.65 → +3.63 pp.
- **Sizing.** The first run of this study was wrong and was thrown away. `BacktestEngine` sized every
  entry at a fixed share count, which is correct for futures and becomes escalating leverage over
  fifteen years of equity compounding — re-entering 82,372 shares of Bajaj Finance at ₹1,000 asks for
  ₹8.2 crore from a ₹5 lakh account. It produced costs of 162–352% of capital and drawdowns above
  100%, and it penalised only the configs, since buy-and-hold never re-enters. Fixed in
  `size_to_equity`, with the flat-series control asserting the two modes agree when nothing
  compounds. **The 95% loss rate here is the number after that fix, not before it.**
- **Guardrails.** No stock traded fewer than 15 times; no stock exceeded 2% share-rounding drag. 126
  of 193 exceed the 50% drawdown guardrail on vol90 — flagged, as the rule requires, and unsurprising
  for single stocks held through 2018-19 and COVID. Buy-and-hold's own median drawdown is 59.6%.

## What this does not establish

- **Nothing about stock futures.** Everything here is cash equity, so it is long-only, and all three
  rules emit SELL as well as BUY — half of each rule was unavailable. NSE stock futures allow the
  short side and are taxed far more lightly (STT 0.02%, sell side only). That is a genuinely
  different experiment and this one does not speak to it.
- **Nothing about a portfolio.** These are 193 independent single-stock accounts, not one book. A
  shared-capital, sector-capped portfolio that is long only the names currently signalling is the
  actionable form of a system like this, and it was deliberately out of scope.
- **Absolute levels are inflated by survivorship.** These are today's F&O members applied back to
  2011. Buy-and-hold's +15.5% median CAGR is not a claim about Indian equities; it is a claim about
  stocks that grew liquid enough to earn F&O status. The bias hits both arms, so the *comparison*
  holds while the *levels* do not. Do not quote the levels.
- **The sign-test p-values are optimistic.** They assume 193 independent observations, and Indian
  equities run 60–70% correlated to the Nifty. The effect sizes are so far from the boundary that
  this changes nothing here, but a marginal result would need the correlation-adjusted breadth.

## Reproducing

```
cd bot
.venv/bin/python -m research.fno.manifest --write     # 210 rows, no token needed
.venv/bin/python -m research.fno.fetch_bars           # ~7 min, resumable
.venv/bin/python -m research.fno.run_configs
.venv/bin/python -m research.fno.walk_forward_run
```

Outputs land in `bot/research/.output/fno/`. The pipeline was validated end-to-end on the eight
cached MCX series before the F&O data existed, where it reproduces `docs/walk-forward-results.md`'s
Gold Mini out-of-sample buy-and-hold Sharpe of 1.99 to the decimal.
