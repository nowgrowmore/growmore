# The NSE F&O equity universe: method, declared before the results

**Written 2026-09-05, before any F&O price data was fetched.** That ordering is the point. Every
metric, guardrail and decision rule below was fixed while the answer was still unknown, so nothing
here can be a rationalisation of a number someone liked. Results, when the data exists, go in
`docs/fno-universe-results.md`.

Code: `bot/research/fno/`. Nothing in this study writes to Neon.

## Why this universe, and what it does and does not fix

The small-cap momentum study (`docs/smallcap-momentum-backtest-results.md`) lost to buying and
holding its own universe, and its post-mortem named the reasons. Two of them are about the
universe itself: the names were illiquid enough that circuit-band fills were never validated, and
"confirming Dhan's `NSE_EQ` data works is not the same as having validated liquidity/circuit-filter
behavior in it."

**The F&O universe fixes exactly that and nothing else.** These 210 stocks are the most liquid on
the exchange — F&O eligibility is itself a liquidity test — so circuit-lock and fill realism stop
being live worries. It does not fix regime (addressed separately, below), it does not fix
survivorship (made *worse*, below), and it does not make a long-only trend rule into alpha.

## The universe: 210 names, derived not scraped

Membership requires all three of an NSE `FUTSTK` row, an `NSE_EQ` cash row, and a Nifty 500 entry.
647 futures rows collapse to 228 underlyings; 18 of those are exchange test symbols
(`011NSETEST`…`181NSETEST`) which have genuine cash-equity security IDs and are excluded only by
the third clause. The result is committed as `bot/research/fno/universe.csv` — the small-cap study
re-scraped NSE on every run and so could not reproduce its own constituent list.

Sector is NSE's own `Industry` field, 18 macro buckets. **Defence is an overlay, not a bucket**,
because NSE has no defence sector and its defence names sit under Capital Goods (BEL, HAL, BDL,
MAZDOCK, COCHINSHIP), Automobile (BHARATFORG) and Chemicals (SOLARINDS). Making it a 19th
exclusive bucket would pull those seven out of the sectors they belong to and distort every count.

| | count | | count |
|---|---|---|---|
| Financial Services | 55 | Consumer Services | 9 |
| Capital Goods | 23 | Power | 8 |
| Healthcare | 16 | Realty | 6 |
| Automobile and Auto Components | 16 | Services | 5 |
| Fast Moving Consumer Goods | 14 | Chemicals | 5 |
| Information Technology | 13 | Construction Materials | 4 |
| Metals & Mining | 10 | Telecommunication | 3 |
| Consumer Durables | 10 | Construction | 3 |
| Oil Gas & Consumable Fuels | 9 | Textiles | 1 |

*Defence overlay (non-exclusive): BDL, BEL, BHARATFORG, COCHINSHIP, HAL, MAZDOCK, SOLARINDS.*

## Fifteen years, not five

Dhan's `NSE_EQ` history reaches 2010, and depth costs nothing — it is the same single API call per
symbol. The documented flaw of **both** prior studies here is that 2021–2026 is one bull regime,
which makes buy-and-hold nearly unbeatable and measures beta rather than edge. Fifteen years spans
the 2011 bear, the 2013 taper tantrum, the 2018–19 midcap crash and COVID, and takes walk-forward
from ~6 folds per stock to ~22.

This makes the verdict more trustworthy. It does not make it more favourable, and it should not be
expected to.

## What is being tested

Three configs, declared in `bot/research/fno/configs.py`, **nothing swept and nothing tuned** —
Phase 6's conclusion was that the grid should shrink, not grow:

| Tag | What it is |
|---|---|
| `rm-macd5-13-5-stop2-trail3` | MACD(5,13,5), 2×ATR initial stop, 3×ATR chandelier trail |
| `rm-ensemble-agree3-stop2-trail3` | 5-speed MACD ensemble, ≥3 agreeing, same ATR block |
| `vol90-rm-ensemble` | the above, plus no new entry in the top decile of trailing realised vol |

Plus **the control**: each stock's own buy-and-hold, through the same engine, the same fills and
the same cost model. Not a footnote — it beat the trading system on five of eight MCX contracts
and on both small-cap universes.

**Long-only is the headline.** Cash equity cannot hold a short overnight, so the symmetric half of
these rules is unavailable. A `--shorts-arm` run is reported separately and labelled
not-executable; it measures what shorting would be worth, not what you could have earned.

## The rules, fixed in advance

1. **The unit of evidence is the config, not the stock.** 210 × 3 is 630 runs and the luckiest of
   630 posts Sharpe ~2.5 on noise. The headline is a paired comparison of each config against each
   stock's *own* buy-and-hold over identical bars — `oos_pairs.py`'s matched-pair discipline
   generalised across stocks. Three configs is three declared trials, and that holds only because
   nothing selects across stocks.
2. **The per-stock leaderboard is published and labelled not-actionable.** Phase 3's rule stands:
   a best-of-N against a negative mean is what noise looks like, and it should not be pursued.
3. **Median and win rate carry the verdict**, with the mean shown beside them for honesty about
   the tail. One stock that trends for a decade can drag a losing config's mean positive.
4. **The sign-test p is an optimistic bound**, stated as such. Indian equities are ~60–70%
   correlated to the Nifty, so 210 stocks are nowhere near 210 independent observations; the
   participation-ratio breadth from `validation/effective_trials.py` is reported alongside it.
5. **Sharpe, return and drawdown are compared separately**, because they will not agree, and the
   disagreement is the finding rather than a presentational problem.
6. **Guardrails flag, never silently drop**: under 15 closed trades, over 50% max drawdown, or
   over 2% share-rounding drag.
7. **Walk-forward runs a fixed config with no re-selection** — Phase 1's actual conclusion was to
   stop selecting, not to walk-forward the selection. Train 504 / test 126 / step 126, geometry
   hard-coded in `growmore_bot/backtest/walk_forward.py`.
8. **Sector is a robustness axis, not a filter.** The question is whether the effect survives
   across sectors or is one sector's boom — not which sector to pick. Picking the best sector after
   the fact is the same error as picking the best stock.

## What will be wrong with the answer regardless

- **Survivorship, and worse than the small-cap study's.** These are today's F&O members applied
  back to 2010; F&O membership is itself a selection on having grown large and liquid. No
  point-in-time membership file exists. Mitigation, not solution: the control runs on the identical
  universe, so the bias hits both arms and the *difference* survives even though the *level* does
  not. Absolute CAGRs from this universe are inflated. Do not quote them.
- **Costs are modelled, impact is not.** STT, exchange, stamp, SEBI and GST are charged per leg,
  and slippage is two ticks a side — which is more defensible in equities than a basis-point
  assumption, since a ₹15 stock genuinely has a ₹0.05 spread. Market impact of a real order is not
  modelled at all. At ₹5 lakh in an F&O-eligible name that is a small error; it would not be in a
  smaller name, which is part of why this universe was chosen.
- **No point-in-time sector labels.** A stock's NSE sector today is applied to its whole history.

## The expected result, written down in advance

**These configs will most likely lose to buy-and-hold on return and beat it on drawdown, on most of
the 210 stocks.** That is what happened on MCX (buy-and-hold won on five of eight), what happened
on small-caps (it won on both universes), and what a long-only trend filter mechanically does to a
long-only asset: it sits out declines, which costs return and buys drawdown.

Recording that here means the result cannot later be presented as a surprise, and — more usefully —
means a *different* result is genuine information. The specific things that would change the
picture: the effect surviving 15 years including two real bear markets rather than only the bull
window; drawdown reduction large enough to be worth the return given up; or the cross-sectional
win rate on Sharpe landing meaningfully above 50% with the sign test agreeing.

A system that gives up return for a materially smaller drawdown is a legitimate thing to own. It is
just not alpha, and the report will say which of the two it found.

## Reproducing

```
cd bot
.venv/bin/python -m research.fno.manifest --write        # 210 rows, no token needed
.venv/bin/python -m research.fno.fetch_bars              # ~5 min, needs a live token
.venv/bin/python -m research.fno.run_configs
.venv/bin/python -m research.fno.walk_forward_run
```

The pipeline is validated end-to-end on the eight cached MCX OHLCV series, which needs no token,
and independently reproduces this repo's published numbers — including Gold Mini's out-of-sample
buy-and-hold Sharpe of 1.99 (`docs/walk-forward-results.md`) to the decimal.
