# Does one instrument's trend improve another's trades? No.

**Run 2026-09-05.** `python -m research.crosstrend.gold_filters_silver`
Series 2021-09-05 → 2026-09-03, real MCX costs, Phase 0 stop fix applied.

## The hypothesis and the gate

Silver Mini is the noisier, higher-beta expression of the same precious-metals macro as Gold
Mini. Gold and silver are cointegrated with a time-varying vector, silver adjusts faster to
disequilibrium, and comovement tightens in exactly the turmoil episodes that produce silver's
drawdowns. So silver's *losing* trades should cluster where gold was not trending — whipsaw a
calmer companion would have vetoed.

This was run as a **gate**, not a feature. Supporting a companion signal in production means
changing `Strategy.on_bar` to accept a second instrument's bar, which touches `strategies/base.py`
and all three engines. That cost is paid only if the effect is real. It measured the effect first
with a date-keyed lookup table (`research/crosstrend/companion.py`) instead.

Rule: a BUY on a day the companion is bearish becomes a HOLD. **Exits are never vetoed** — a
filter that can block an exit traps the position and is a bug, not a filter.

## Result: 12 of 45 combinations improved, mean **−0.255** Sharpe

Every combination tried: 5 instrument pairs × 3 companion signals × 3 target variants.

The specific hypothesis — gold filtering silver — fails on all nine of its combinations:

| Target variant | Companion signal | Trades | CAGR | Sharpe | MaxDD | ΔSharpe |
|---|---|---|---|---|---|---|
| rm macd5-13-5 | *(unfiltered)* | 88 | 35.9% | 1.49 | 20.5% | — |
| rm macd5-13-5 | GOLDM macd12-26-9 | 33 | 27.5% | 1.18 | 22.2% | −0.30 |
| rm macd5-13-5 | GOLDM macd5-13-5 | 51 | 30.6% | 1.35 | 23.5% | −0.14 |
| rm macd5-13-5 | GOLDM ensemble-agree3 | 35 | 26.7% | 1.13 | 20.8% | −0.36 |
| rm ensemble-agree3 | *(unfiltered)* | 49 | 39.3% | 1.52 | 16.3% | — |
| rm ensemble-agree3 | GOLDM macd12-26-9 | 23 | 7.0% | 0.41 | 25.0% | **−1.11** |
| rm ensemble-agree3 | GOLDM macd5-13-5 | 38 | 38.3% | 1.52 | 16.6% | −0.00 |
| rm ensemble-agree3 | GOLDM ensemble-agree3 | 24 | 10.4% | 0.58 | 19.8% | −0.94 |

The best case is a dead heat. Everything else is worse, several catastrophically.

## Why — and it corroborates the currency result

Look at what the filter actually does: it removes roughly half the trades (88 → 33–51) and
removes **more than half the return**, while the drawdown does not improve and sometimes worsens.
So the vetoed trades were not disproportionately losers. They were, if anything, disproportionately
*winners*.

That makes sense. Silver is roughly half industrial demand; its big moves include squeezes and
supply episodes that gold does not participate in. Requiring gold's agreement systematically
discards exactly the silver-specific moves that are silver's edge — the same edge the currency
decomposition found surviving in USD terms while gold's did not.

The one caveat on the filter's construction: a slower companion vetoes more (GOLDM macd12-26-9
blocks 56 of 89 entries) and does worse, which is consistent with the filter removing signal
rather than noise. A gentler filter would converge on doing nothing.

## Verdict

**Do not build the companion-bar interface change.** The gate is answered. This is what the gate
was for — the study cost a day; the interface change would have touched `base.py`, the backtest,
paper and live engines and the scheduler.

The best result anywhere in the run was COPPER filtered by GOLDM macd5-13-5 at +0.24 Sharpe.
With 45 combinations tried, a best-of-45 at +0.24 against a mean of −0.255 is what noise looks
like, and it should not be pursued.

## A bug found in the process, recorded because it nearly produced a fake result

`trend_states` originally read a stance from `debug_state()` by looking for `stance` or a
`macd`/`signal` pair. `EnsembleTrendStrategy.debug_state()` exposes neither — it reports vote
*counts*. So the ensemble companion returned an opinion on **zero** days, and because an unknown
day vetoes, every ensemble-filtered run reported exactly 0 trades and a Sharpe of 0.00. That
reads as a real, catastrophic result rather than as a broken adapter, and it was in the first
output of this study. `trend_states` now raises if a companion never forms an opinion, and there
is a regression test for it. Worth noting more generally: `Strategy` has no common field for
"which way do you currently lean", which is the underlying reason a production version would
need an interface change rather than an adapter.

## Reproducing

```bash
cd bot
python -m research.crosstrend.gold_filters_silver
python -m research.crosstrend.gold_filters_silver --pairs SILVERM:GOLDM
```
