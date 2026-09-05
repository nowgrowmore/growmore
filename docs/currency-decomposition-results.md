# How much of Gold Mini's return is gold, and how much is the rupee?

**Run 2026-09-05.** `python -m research.currency.inr_decomposition`
Series 2021-09-05 → 2026-09-03. USD/INR from FRED `DEXINUS` (Federal Reserve H.10),
forward-filled over holidays only, never backward.

## Why

MCX bullion is not a bet on bullion:

```
MCX price = (international price in USD) x USDINR x (1 + import duty)
```

A long MCX gold position is simultaneously long metal and **short rupee**. Over the backtest
window USD/INR went **72.98 → 95.38: +30.7% total, +5.51% a year of pure tailwind** that a
long-only trend follower collects for free and that has nothing to do with a MACD crossover.
Every published CAGR in this repo includes that, undifferentiated. Nobody had checked the split.

Method: divide the whole OHLC by that day's rate to get a USD-denominated series, then run the
identical strategies on both with identical costs. Not a new strategy, so it costs nothing
against the trials budget.

## Gold Mini — a third of the return, and a third of the Sharpe, is the rupee

| Variant | INR CAGR | INR Sharpe | USD CAGR | USD Sharpe | ΔSharpe |
|---|---|---|---|---|---|
| buy & hold | +220.3% total | 1.48 | +145.1% total | 1.13 | **−0.35** |
| rm ensemble-agree3 | 20.6% | 1.62 | 13.7% | 1.10 | **−0.52** |
| rm macd5-13-5 | 21.1% | 1.57 | 16.5% | 1.30 | −0.27 |
| rm macd12-26-9 | 20.5% | 1.55 | 13.7% | 1.11 | −0.44 |
| macd5-13-5 (bare) | 22.0% | 1.43 | 17.6% | 1.21 | −0.22 |
| ensemble-agree3 (bare) | 16.3% | 0.99 | 8.5% | 0.56 | −0.43 |

The headline `risk_managed ensemble-agree3` drops from **1.62 to 1.10** and from 20.6% to 13.7%
CAGR once the rupee is removed.

Note which way the strategies move relative to the benchmark. Buy-and-hold loses 0.35 Sharpe to
the currency — that is just the tailwind. The **ensemble loses more (0.52), and so does
MACD(12,26,9) (0.44)**. A signal that were purely reading gold would lose *less* than
buy-and-hold, not more. Losing more means the trend rule is partly tracking the rupee itself,
which is exactly what you would expect: USD/INR is a smoother, lower-volatility, more persistently
trending series than gold, so it is easier for a trend follower to ride. The slower and smoother
the signal, the more of its apparent edge is currency — which is why the five-speed ensemble is
the worst affected and the fast bare MACD the least.

## Silver Mini — the edge is in the metal

| Variant | INR CAGR | INR Sharpe | USD CAGR | USD Sharpe | ΔSharpe |
|---|---|---|---|---|---|
| buy & hold | +265.8% total | 0.99 | +179.9% total | 0.81 | −0.17 |
| rm ensemble-agree3 | 39.3% | 1.52 | 33.8% | 1.40 | **−0.12** |
| rm macd5-13-5 | 35.9% | 1.49 | 32.1% | 1.42 | **−0.07** |
| rm macd12-26-9 | 36.3% | 1.41 | 30.7% | 1.24 | −0.17 |
| macd5-13-5 (bare) | 31.7% | 1.27 | 27.7% | 1.17 | −0.10 |
| ensemble-agree3 (bare) | 32.9% | 1.22 | 27.2% | 1.07 | −0.15 |

Silver barely moves. Its risk-managed variants lose 0.07–0.12 Sharpe against buy-and-hold's 0.17
— i.e. they lose *less* than the benchmark, so the signal is reading the metal and not the
currency. Silver is roughly three times as volatile as gold, so a 5.5%/yr FX drift is
proportionally small noise inside it.

## What this means alongside the walk-forward result

Two independent tests, run on different principles, agree:

| | Gold Mini | Silver Mini |
|---|---|---|
| In-sample DSR (published) | **0.96 — significant** | 0.73 — luck |
| Out-of-sample vs buy & hold | Sharpe 2.08 vs 1.99, return 109% vs **161%** | Sharpe 2.09 vs 1.53, return **351%** vs 273% |
| Sharpe surviving currency removal | 1.62 → **1.10** | 1.52 → **1.40** |

**Gold Mini's headline is largely rupee depreciation plus a bull market, with a genuine
drawdown-reduction benefit on top. Silver Mini's is a real signal that the deflated-Sharpe test
mislabelled as luck.** The in-sample ranking has these two backwards.

## Caveats

- Costs are charged in the currency the series is quoted in, so the USD run slightly understates
  the rupee cost of trading. At 2–5 bps a round trip on a daily book this is far below the
  effect measured.
- **Import duty is a real confound in the INR series only.** It moved 7.5% → 12.5% (Jul 2022),
  15% → 6% (Budget, 23 Jul 2024) and back toward 15% (Feb 2025). Each is a one-day step in the
  INR price with no counterpart in the USD one — the July 2024 cut in particular was a ~9%
  single-session drop in MCX gold that global gold never saw. That penalises the INR series at
  those three dates, so if anything it *understates* how much of the INR result is currency.
- FRED's DEXINUS is a Federal Reserve reference rate, not RBI's. They differ by a few paise, far
  below the effect being measured. RBI's own series is only published through a portal that
  resists scripting.
- OHLC is divided by a single daily rate, so intraday FX movement is not modelled. Irrelevant at
  daily resolution.

## Reproducing

```bash
cd bot
python -m research.currency.inr_decomposition
python -m research.currency.inr_decomposition --symbols GOLDM SILVERM COPPER
```
