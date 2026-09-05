# Walk-forward validation: the first out-of-sample numbers this project has ever had

**Run 2026-09-05.** `python -m research.validation.walk_forward_run`
Grid hash `5a675271ac80` (18 variants). Geometry: train 504 bars / test 126 / step 126,
rolling. Selection metric declared before running: **Sharpe**, among training runs closing at
least 8 trades. Real MCX costs and slippage on every leg. Series from
`research/dailydata/` (duplicate-repaired, corrupt bars dropped), with the Phase 0
trailing-stop lookahead fixed.

## Why this exists

Every number in `docs/backtest-results.md` is in-sample. The sweep ran 264 variants over one
window and reported the best; `deflated_sharpe.py` then discounted that number for the size of
the search. Deflation is a correction, not evidence. This asks the question deflation cannot:
**if you had picked a variant using only the data available at the time, would it have made
money in the six months that followed?**

Four things are measured over the same out-of-sample bars:

- **re-selected** — re-pick the best training-window variant at every fold
- **fixed null** — `risk_managed macd12-26-9` always, never re-picked
- **incumbent** — `risk_managed macd5-13-5`, what runs in paper today
- **buy & hold** — one lot held through every test window, same costs

## The headline

```
inst                     Sharpe     total    maxDD
--------------------------------------------------
ALUMINI    re-selected     0.75      4.0%     3.0%
           fixed null      0.46      5.6%     6.2%
           incumbent       1.49     22.7%     7.3%
           buy & hold      1.50     28.5%    13.5%
--------------------------------------------------
COPPER     re-selected     0.90     32.2%    13.6%
           fixed null      1.31     56.2%    14.2%
           incumbent       1.17     54.5%    17.3%
           buy & hold      1.30     85.7%    18.9%
--------------------------------------------------
CRUDEOILM  re-selected     0.39     11.1%    24.9%
           fixed null      0.01    -15.6%    51.5%
           incumbent       0.56     26.0%    47.4%
           buy & hold      0.95     66.9%    38.8%
--------------------------------------------------
GOLDM      re-selected     1.25     51.6%    13.7%
           fixed null      1.91     96.0%     9.9%
           incumbent       2.08    108.8%     8.1%
           buy & hold      1.99    161.0%    18.6%
--------------------------------------------------
LEADMINI   re-selected    -1.05     -2.9%     4.3%
           fixed null     -3.29    -29.3%    29.6%
           incumbent      -1.99    -21.1%    21.8%
           buy & hold      0.32      3.5%     4.6%
--------------------------------------------------
NICKEL     re-selected     0.57     16.7%    21.8%
           fixed null      0.79     28.0%    12.6%
           incumbent       0.64     19.8%    21.8%
           buy & hold      0.54     28.2%    31.0%
--------------------------------------------------
SILVERM    re-selected     1.61    235.8%    40.2%
           fixed null      2.09    351.1%    17.2%
           incumbent       2.07    313.3%    21.3%
           buy & hold      1.53    273.1%    45.4%
--------------------------------------------------
ZINCMINI   re-selected    -0.09     -2.0%    11.3%
           fixed null      0.07     -0.0%     8.5%
           incumbent      -0.36     -5.9%    13.5%
           buy & hold      1.20     20.8%    12.9%
--------------------------------------------------

mean OOS Sharpe:  selected 0.54 | fixed 0.42 | incumbent 0.71 | buy & hold 1.17
re-selecting beat the fixed null on 3 of 8
the fixed null beat buy-and-hold on 3 of 8
```

## Three findings, in order of how much they should change what we do

### 1. Buy-and-hold beats the trading system on mean out-of-sample Sharpe, 1.17 to 0.71

Not on every instrument, but on the average and on five of eight. This is the number that
matters and it has never been computed before, because no backtest in this repo carried a
buy-and-hold control.

The honest caveat cuts both ways: the out-of-sample span lands on 2023-11 to 2026-05, the
strongest stretch of the commodity bull run in the dataset, so buy-and-hold is being flattered.
But that is exactly the point — **the whole five-year window is one bull run**, so the in-sample
sweep that produced our published rankings was largely measuring which trend variant best tracks
an uptrend. That is a question about beta, not about edge.

### 2. Silver Mini is the real result, and its in-sample DSR said it was luck

Silver Mini is the **only** instrument where a strategy beats buy-and-hold on all three axes at
once: Sharpe 2.09 vs 1.53, total return 351% vs 273%, and max drawdown **17.2% vs 45.4%**. It
earns more, at higher risk-adjusted return, while more than halving the worst loss. That is what
a trend system is supposed to do, and it is the only place here that it actually does it.

Silver Mini's in-sample DSR was **0.73 — "not distinguishable from luck."**

### 3. Gold Mini is a drawdown tool, not an alpha source — and its in-sample DSR said the opposite

Gold Mini's incumbent posts Sharpe 2.08 against buy-and-hold's 1.99: a rounding error. It
returns **108.8% where holding returned 161%**. What it does buy is a drawdown of 8.1% instead
of 18.6%. That is a real and valuable thing, but it is risk reduction, not edge, and it should
not be described as edge.

Gold Mini's `risk_managed ensemble-agree3` was the single result in the 264-run sweep clearing
the DSR 0.95 significance bar.

**So the deflated-Sharpe ranking and the walk-forward ranking point in opposite directions.**
The instrument that looked significant is adding little beyond a smaller drawdown; the
instrument that looked like luck is the one adding genuine value. Deflated Sharpe corrects for
how much you searched. It cannot tell you that the thing you were searching for was beta.

## Does re-selecting a variant each fold help?

No. Mean out-of-sample Sharpe 0.54 re-selecting vs 0.42 for a fixed variant never re-picked —
nominally ahead, but it beat the fixed null on only 3 of 8, and it **lost badly on the two
instruments that matter**: Gold Mini 1.25 vs 1.91, Silver Mini 1.61 vs 2.09.

The mechanism is visible in the fold table. Gold Mini fold 4 selected `rm-sma5-20` on a training
Sharpe of 2.48 — the best training number in the whole run — and it returned **-5.4%** out of
sample. Silver Mini's selector, by contrast, locked onto one variant for four consecutive folds
and did fine. Selection helps when it is stable and hurts when it chases; on this much data it
mostly chases.

**Recommendation: stop selecting. Run a fixed variant per instrument.** This is the answer the
plan pre-committed to accepting if it came back this way.

## Things this run does NOT establish

- **It is not a fresh 2.0 Sharpe.** The out-of-sample Sharpes are *higher* than the in-sample
  ones (Gold Mini 2.08 OOS vs 1.57 full-period). That is not the strategy improving, it is the
  test window being the easy half of the data. Do not quote these as forward expectations.
- **Only five folds** for Gold Mini and Silver Mini; three for Crude Oil Mini; **two** for
  Aluminium Mini, Lead Mini and Zinc Mini, whose Dhan history only starts in 2023. Aluminium
  Mini fold 1 closed *zero* trades. Treat the four short-history instruments as unmeasured.
- **Lead Mini is actively dangerous** on this evidence: fixed null -3.29 Sharpe / -29.3%,
  incumbent -1.99 / -21.1%, against a buy-and-hold that did nothing much. Nothing should be
  enabled there.
- Costs are modelled, the polled-stop limitation is not. A backtest stop fills at the stop
  level; the live bot polls every 300s. See `docs/pending-actions.md`.

## Reproducing

```bash
cd bot
python -m research.dailydata.fetch          # once; caches 5y daily bars to parquet
python -m research.validation.walk_forward_run
python -m research.validation.walk_forward_run --symbols GOLDM SILVERM
```

The cache makes this exactly reproducible: the same bars every time, no live Dhan token, and
no write to Neon.
