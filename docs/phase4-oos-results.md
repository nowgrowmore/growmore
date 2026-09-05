# Five ideas for Gold Mini and Silver Mini, measured out-of-sample. One works.

**Run 2026-09-05.** `python -m research.validation.oos_pairs`
Same folds as `docs/walk-forward-results.md` (train 504 / test 126 / step 126, 5 folds,
out-of-sample 2023-11 → 2026-05). Real MCX costs. Phase 0 stop fix applied.

## Method: matched pairs, not a ranking

Each idea is scored as a **fixed** variant over the out-of-sample windows and compared against
the one variant it differs from by exactly one decision. A paired comparison is not a search —
the difference is attributable, and nothing is being cherry-picked from a leaderboard. **Picking
the best row of the table below would be a search**, and would have to be counted against the
effective-trials budget. Don't.

Nothing here was added to the in-sample sweep, so none of it inflates the trials count that the
published DSR figures are discounted by.

## Result: 6 of 30 pairs better, 16 worse, mean **−0.212** Sharpe

### Gold Mini

| Idea | Baseline SR / ret / trades | Treatment SR / ret / trades | ΔSharpe |
|---|---|---|---|
| 4b buffer 0.10 | 2.08 / 108.8% / 41 | 1.94 / 107.2% / 24 | −0.13 |
| 4b buffer 0.25 | 2.08 / 108.8% / 41 | 0.87 / 38.8% / 18 | **−1.21** |
| 4b buffer 0.25 (slow MACD) | 1.91 / 96.0% / 23 | 1.35 / 43.0% / 8 | −0.56 |
| **4c vol filter p90** | 2.08 / 108.8% / 41 | 2.40 / 97.4% / 29 | **+0.32** |
| 4c vol filter p80 | 2.08 / 108.8% / 41 | 2.14 / 69.6% / 22 | +0.06 |
| **4c vol filter p90 (ensemble)** | 2.02 / 100.5% / 23 | 2.15 / 97.4% / 15 | **+0.13** |
| 4d time stop 30 bars | 2.08 / 108.8% / 41 | 2.08 / 108.8% / 41 | 0.00 |
| 4d time stop 60 bars | 2.08 / 108.8% / 41 | 2.08 / 108.8% / 41 | 0.00 |
| 4e stop 1.5 / trail 2 | 2.08 / 108.8% / 41 | 1.88 / 96.3% / 41 | −0.20 |
| 4e stop 3 / trail 4 | 2.08 / 108.8% / 41 | 2.16 / 128.8% / 41 | +0.08 |
| 4a EMA(112) vs fast MACD | 1.78 / 114.2% / 41 | 1.88 / 158.5% / **2** | +0.10 |
| 4a EMA(112) + stops | 2.08 / 108.8% / 41 | 0.86 / 29.6% / **3** | **−1.22** |

### Silver Mini

| Idea | Baseline SR / ret / trades | Treatment SR / ret / trades | ΔSharpe |
|---|---|---|---|
| 4b buffer 0.10 | 2.07 / 313.3% / 48 | 1.91 / 276.8% / 27 | −0.16 |
| 4b buffer 0.25 | 2.07 / 313.3% / 48 | 2.06 / 327.1% / 11 | −0.01 |
| 4c vol filter p90 | 2.07 / 313.3% / 48 | 1.89 / 159.1% / 36 | −0.19 |
| 4c vol filter p80 | 2.07 / 313.3% / 48 | 1.52 / 96.3% / 30 | −0.55 |
| **4c vol filter p90 (ensemble)** | 2.29 / 383.4% / 27 | **2.49 / 405.1% / 21** | **+0.20** |
| 4d time stop 30 / 60 bars | 2.07 / 313.3% / 48 | 2.07 / 313.3% / 48 | 0.00 |
| 4e stop 1.5 / trail 2 | 2.07 / 313.3% / 48 | 1.85 / 225.2% / 49 | −0.22 |
| 4e stop 3 / trail 4 | 2.07 / 313.3% / 48 | 1.96 / 294.0% / 47 | −0.12 |
| 4a EMA(112) vs fast MACD | 1.62 / 240.3% / 47 | 1.22 / 202.8% / 12 | −0.41 |
| 4a EMA(112) + stops | 2.07 / 313.3% / 48 | 0.83 / 71.9% / **15** | **−1.25** |

## The one thing that works

**A 90th-percentile realised-volatility admission filter on the risk-managed ensemble**, and it
is the only treatment that improves on *both* instruments: **+0.13 on Gold Mini, +0.20 on Silver
Mini.** On Silver Mini that combination posts **out-of-sample Sharpe 2.49 with +405% return** —
the best figure anywhere in this study, against buy-and-hold's 1.53 / +273%.

Note where the gain really comes from: the risk-managed *ensemble* baseline is already 2.29 on
Silver Mini, ahead of the incumbent MACD(5,13,5) at 2.07. The ensemble is the larger win; the
volatility filter adds to it.

Mechanism: the filter refuses to open while 20-day realised volatility sits in the top decile of
its own trailing two-year distribution. It removes 6 of 27 entries and *raises* total return. It
is deliberately binary — continuous volatility targeting is not expressible at this account size
(Gold Mini at ₹5 lakh asks for ~0.3 lots), but "one lot or none" is.

`RealizedVolCalculator` had been sitting in `indicators.py`, fully tested, imported by nothing
but its own test.

## The four that don't, and why

**4d — the time stop does exactly nothing.** Not a plumbing bug: `max_bars_held` reaches the
strategy correctly. It never binds. The longest Gold Mini trade in five years is 32 calendar days
(~22 trading bars) and only 1 of 86 trades exceeds 30 days, so a 30-bar limit has nothing to cut.
This closes a real question — `max_bars_held` has been implemented, tested, and never once set by
any sweep since the risk layer was built. It is dead code because there is nothing for it to do,
not because nobody wired it up. Leave it unset.

**4b — the no-trade buffer is actively harmful.** At θ=0.25 on Gold Mini it cuts trades 41 → 18
and return 108.8% → 38.8% for a Sharpe of 0.87. The turnover-reduction argument from the trend
literature assumes the marginal trades are noise; here they are where the return is. Worse on
both instruments at θ=0.10 too.

**4a — the slow EMA is untestable on this data, and lethal with stops.** EMA(112) fires **2
trades** on Gold Mini and 12 on Silver Mini across the whole out-of-sample span. Its +0.10 on
gold rests on two observations and means nothing. Wrapped in a 2×ATR stop it is the worst result
in the study (−1.22, −1.25) for an understandable reason: a 112-day signal takes months to
generate a re-entry, so once the stop fires the strategy sits flat through the recovery. **Slow
trend and tight stops are incompatible** — the literature's slow-EMA result assumes a
continuously-scaled position, not a stop-and-wait one. The predicted caveat held: this dataset
cannot test a 112-day signal.

**4e — the current 2.0/3.0 ATR multiples are already about right.** Tightening to 1.5/2 is worse
on both instruments and both base strategies. Widening to 3/4 is mixed (+0.08 gold, −0.12
silver). The technical-debt note calling for per-instrument stop calibration is now answered:
there is no evidence for changing them, and specifically no evidence that Silver Mini wants a
different multiple from Gold Mini.

## What to actually do

1. **Adopt `vol_filtered(risk_managed(ensemble_trend, agree3, stop2, trail3), p90)` for Silver
   Mini.** It is the only idea that survived, it survived on both instruments, and it targets the
   thing that was actually wrong.
2. **Do not adopt the buffer, the time stop, the slow EMA, or a different stop multiple.**
3. Read all of this next to `docs/walk-forward-results.md`: the out-of-sample window is the
   easy half of the data, and on Gold Mini buy-and-hold still returns more (161%) than any
   strategy here. These Sharpes are not forward expectations.

## Reproducing

```bash
cd bot
python -m research.validation.oos_pairs
python -m research.validation.oos_pairs --symbols GOLDM SILVERM COPPER
```
