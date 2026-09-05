# Technical Debt / Known Limitations

- **(Partially CLOSED 2026-09-05) Dhan's stitched 5-year series does NOT appear to contain fake
  roll gains -- checked against real spot gold.** The open worry (below, and in
  `docs/architecture.md`) was that `instruments.security_id` for GOLDM is an Oct-2026 contract that
  did not exist in 2021, so Dhan is returning something spliced by an unverified method. If it
  splices raw prices in contango, every monthly roll injects a small fake upward jump -- which
  would matter enormously now, because a buy-and-hold benchmark is long 100% of the time and would
  collect *every* fake gap while a strategy that is long ~55% of the time collects only some. That
  would bias the central "buy-and-hold beats the system" finding in buy-and-hold's favour.

  Test: convert the MCX series to USD per troy ounce using the daily reference rate and compare
  against actual spot gold at both ends of the window.

  | | Sept 2021 | Sept 2026 | change |
  |---|---|---|---|
  | MCX-implied, duty-inclusive | $2,022/oz | $4,957/oz | **+145.1%** |
  | Actual spot gold | ~$1,800/oz | ~$4,490/oz | **~+150%** |
  | implied premium over spot | ~12.6% | ~10.4% | narrowed |

  The stitched series shows **slightly less** than spot gold, not more. Raw contango splicing would
  show more. The residual gap is explained by the import duty narrowing (15% -> 6% in the July 2024
  budget, partly restored since), which mechanically costs the MCX holder relative to the metal.
  So the benchmark is sound and, if anything, marginally conservative.

  What this does NOT close: there is still no `instrument_contracts` table, `contract_rollover.py`
  still overwrites `security_id` in place so roll history is still being destroyed, and no basis
  history exists or can be recovered. This test bounds the error on *returns*; it does not make the
  series auditable.

- **(Measured 2026-09-05) Rolling a buy-and-hold position costs ~2.4 bps a round trip -- immaterial.**
  Gold Mini Rs 370 and Silver Mini Rs 290 per roll at current notionals (statutory + tick
  slippage). Even at monthly rolls that is ~1.5% over five years, against a benchmark return of
  +161%. The backtest engine models no rolls at all, so its buy-and-hold figure omits this; the
  omission is smaller than the rounding on the numbers it is compared against.

- **(Found + fixed 2026-09-05) The trailing stop tested a level it built from the SAME bar --
  lookahead, and it was flattering Gold Mini while badly hurting Silver Mini.**
  `risk/wrapper.py:_advance` ratcheted the chandelier stop using bar B's own high, and
  `_exit_reason` then tested bar B's own low against that just-ratcheted level. OHLC cannot tell
  you whether the high came before the low, so a level built from B only becomes live for B+1.
  It was also redundant: `backtest/engine.py:132-166` already implements the honest version
  (`armed_stop` set from a closed bar, tested against the NEXT bar's range, filled at
  `min(bar.open, stop)` so a gap-through fills at the open). Fixed by testing the breach against
  the stop as it stood ENTERING the bar; the ratchet still runs and still arms the engine for the
  next bar. Two smaller things fixed alongside: the engine armed `armed_stop` only when
  `position_qty > 0`, so a short was never protected by the engine-level check; and the wrapper's
  exit signal carried no `stop_price`, so a stale level stayed armed for that bar.

  Measured on the identical cached series (`research/dailydata/`, so the only variable is the code
  change). The bare, non-risk-managed variants and the `notrail` variant are unchanged to the
  decimal, which is the control -- the fix touches trailing stops and nothing else. The pre-fix
  column reproduces `docs/backtest-results.md` exactly:

  | Instrument | Variant | CAGR | Sharpe | MaxDD |
  |---|---|---|---|---|
  | GOLDM | rm ensemble-agree3 | 22.2% -> 20.6% | 1.79 -> **1.62** | 10.0% -> 12.3% |
  | GOLDM | rm macd5-13-5 | 22.3% -> 21.1% | 1.70 -> **1.57** | 8.8% -> 11.7% |
  | GOLDM | rm macd12-26-9 | 19.2% -> 20.5% | 1.56 -> 1.55 | 10.3% -> 10.8% |
  | SILVERM | rm macd5-13-5 | 32.6% -> 35.9% | 1.33 -> **1.49** | 28.4% -> 20.5% |
  | SILVERM | rm macd12-26-9 | 21.6% -> 36.3% | 1.00 -> **1.41** | 22.4% -> 17.5% |
  | SILVERM | rm ensemble-agree3 | 26.2% -> 39.3% | 1.19 -> **1.52** | 22.0% -> 16.3% |
  | GOLDM | macd5-13-5 (bare) | 22.0% -> 22.0% | 1.43 -> 1.43 | 19.1% -> 19.1% |
  | ZINCMINI | rm boll20-2.5-notrail | 15.3% -> 15.3% | 1.89 -> 1.89 | 6.5% -> 6.5% |

  The direction was not predicted and is worth understanding. The bug forced an exit one bar early
  whenever a bar's own high dragged the chandelier above its own low. On Gold Mini -- a smooth
  trend with shallow pullbacks -- those premature exits happened to dodge noise, so removing them
  costs 0.13-0.17 Sharpe. On Silver Mini -- violent, wide-range bars -- the same rule fired
  constantly and cut winners short; removing it is worth +0.16 to +0.41 Sharpe and takes 5-8
  points off the drawdown. **The single best Silver Mini result in the book is now
  `risk_managed ensemble-agree3` at 39.3% CAGR / 1.52 Sharpe / 16.3% DD**, and it arrived from a
  correctness fix rather than a new variant, so it costs nothing against the trials budget.
  Every `risk_managed` figure published before 2026-09-05 evening is wrong in one direction or the
  other and should not be quoted.

- **(Noted 2026-09-05) The sweep's most "significant" result is a 6-trade result.**
  `risk_managed boll20-2.5-notrail` on ZINCMINI -- Sharpe 1.89, DSR 0.97, the only entry the
  deflated-Sharpe run called `significant` -- closes **6** trades in three and a half years, well
  under `run_all.py`'s own `DEFAULT_MIN_TRADE_COUNT = 15`. Its profit factor of 18.0 is the tell.
  The guardrail flags low trade counts but never drops them, and the DSR calculation does not look
  at trade count at all, so a six-observation fluke ranked above everything. Do not act on it.

- **(Found + fixed 2026-09-05) Dhan's 5-year daily series overlaps TWO CONTRACT MONTHS at every
  roll, and the wrong one was being kept.** Repeated dates in a single `security_id`'s history are
  not redundant copies: for Gold Mini, **41 of 43 repeated dates carry different OHLC and different
  volume**. 2022-10-09 returns one bar on 5,603 lots and another on 15,000, about 1% apart in price
  — the high-volume bar is the liquid front month, the other is the expiring contract nobody is
  trading. Counts across the universe: GOLDM 43 repeated dates and 10 unusable bars out of 1,260;
  COPPER 35 and 24 out of 1,252; SILVERM 3 and 3 out of 1,220. A first version of the bar validator
  kept whichever bar arrived first, which picks the illiquid contract roughly half the time and
  injects a fake ~1% gap at every roll — about 12 a year, in a series every strategy reads as
  continuous prices. **Fixed:** duplicates now resolve to the higher-volume bar, and validity is
  checked before the volume comparison so a corrupt zero-price bar with large volume cannot win.
  This is the concrete form of the contract-continuity concern flagged earlier, and it means every
  backtest number produced before 2026-09-05 sat on a series with roughly a dozen fake gaps a year.
- **(2026-09-05) Shorting is built in the backtest and makes results WORSE here — do not enable
  it.** `BacktestEngine(allow_shorts=True)` implements signed quantity (negative = short),
  direction-aware stops, and reversal as two cost-paying legs. Measured on the same strategies and
  data:

  | Strategy / instrument | Long-only | Long + short |
  | --- | --- | --- |
  | Risk-managed ensemble / Gold Mini | 22.0% CAGR, Sharpe 1.77, 10% DD | **−0.0%, 0.15, 64% DD** |
  | Ensemble / Gold Mini | 16.1%, 0.98, 29% | 0.6%, 0.25, 66% |
  | MACD(5,13,5) / Gold Mini | 21.9%, 1.42, 19% | 16.5%, 0.72, 33% |
  | MACD(5,13,5) / Silver Mini | 31.7%, 1.27, 38% | 33.6%, 1.08, 34% |
  | Risk-managed ensemble / Copper | 13.1%, 1.16, 11% | 16.9%, 1.23, 14% |

  **Sharpe falls in all nine pairings tested**, and the best result in the whole project
  (risk-managed ensemble on Gold Mini) is destroyed outright. The reason is not subtle: 2021–2026
  was a secular precious-metals bull market, and shorting gold through it is simply the wrong trade.
  The external evidence that long/short roughly doubles Sharpe comes from diversified multi-asset
  CTA portfolios over multi-decade horizons — it does not transfer to two precious metals in one
  bull window. The capability stays available behind a default-off flag for when a bear regime
  gives it something to work with; **it is not enabled anywhere, and the paper and live engines
  remain long-only and untouched.**
- **(2026-09-05) `vwap_session_bounce` finally has a backtest, and I was wrong about it.** It has
  been enabled in paper trading with no evidence at all, justified by "today's live session VWAP
  doesn't exist in historical bars". Dhan's intraday endpoint makes that false — 5-minute MCX
  candles, 5 years, 90 days a request, reachable through the `interval` argument
  `DhanClient.get_historical_ohlc` has always had and nothing had ever used. I expected a clear
  negative result. It isn't:

  | Instrument | Sessions | Trades | Signals/day | Net P&L | Gross | Costs | Win% |
  | --- | --- | --- | --- | --- | --- | --- | --- |
  | Gold Mini | 108 | 46 | 4.3 | **+₹46,902** | ₹64,260 | ₹17,358 | 54.3% |
  | Silver Mini | 225 | 129 | 3.5 | **+₹132,152** | ₹171,620 | ₹39,468 | 49.6% |
  | Copper | 90 | 49 | 3.1 | **−₹216,225** | −₹158,125 | ₹58,100 | 44.9% |

  Profitable net of real costs on both bullion contracts, clearly losing on Copper. Against 1 lot of
  notional that is roughly 7–11% annualised at 1x — modest, but real, and it survives a cost load
  that eats 23–27% of gross. **Recommendation: keep the Gold Mini config enabled, do not add a
  Copper one.**

  Four caveats that matter more than the numbers:
  - **The samples are months, not years** (see the next item) — 46 trades on Gold Mini.
  - **This is not quite the strategy that is running.** The replay detects crossings on 5-minute bar
    closes; the live bot detects them from a 5-minute LTP *snapshot*, which catches a different set
    of crossings. A positive backtest validates a near-neighbour, not the incumbent.
  - **Session VWAP is reconstructed** from 5-minute bars, not the exchange's own trade-by-trade
    figure. The strategy triggers on a crossing, so a small error near the crossing point flips
    signals. This has not been calibrated against Dhan's live `average_price` yet.
  - **Half the strategy is dead code.** It is long-only in a long-only engine, so its entire
    bearish-CPR SELL branch can never open a position.
- **(2026-09-05) Intraday history is per-contract and only a few months deep, unlike the daily
  series.** Fetching 5-minute bars for the current front-month `security_id` returns Gold Mini from
  2026-04-07, Copper from 2026-05-01, Silver Mini from 2025-10-20 — because each `security_id` is
  one contract month and intraday data exists only for as long as that contract has been listed.
  The *daily* endpoint returns five years for the same ids, which all but confirms Dhan serves a
  stitched continuous series for daily and raw per-contract data for intraday. Two consequences: any
  intraday backtest is capped at a few months per instrument until contracts are stitched
  explicitly, and the long-standing question of how the 5-year daily series is spliced together
  (`docs/technical-debt.md`'s contract-continuity item) is now more pressing, since we know the two
  endpoints behave differently.
- **(2026-09-05) The multi-lookback trend ensemble, WITH stops, is the only result in the sweep
  that is statistically significant.** `EnsembleTrendStrategy` runs five MACD speeds (5/13/5 through
  26/52/18) and acts on their majority vote, so there is no lookback to select and therefore no
  selection to be biased by. Wrapped in the ATR risk layer on Gold Mini it posts **Sharpe 1.77, max
  drawdown 10.1%, CAGR 22.0% over 49 trades — and DSR 0.95**, the only entry in a 216-run sweep to
  clear the conventional significance bar.

  What makes that interesting is that **neither half is best on its own**. The bare ensemble on Gold
  Mini scores Sharpe 0.98 with a 29.2% drawdown — clearly *worse* than the single best MACD variant
  (1.42 / 19.1%), exactly as expected: an ensemble trades away the lucky tail. Bare MACD with stops
  reaches 1.68 / 8.8% but only DSR 0.92. It is the combination that wins, and it wins on the metric
  that accounts for how many things were tried.

  Two design details worth keeping. **Votes are read from each member's STATE (macd vs its signal
  line), not from the BUY/SELL events it emits** — a first implementation used events and never
  traded at all, because a MACD member only speaks on a crossing and in a smooth sustained trend it
  crosses once before the ensemble is warm and then stays silent forever. And **the member speeds
  must be well spread**: with clustered speeds (2/3/2, 3/5/2, 5/9/3) every member flips on the same
  bar and the vote adds nothing, whereas the shipped speeds defect in sequence on a pullback (2 of 5
  on the first down bar, majority on the second), which is the entire point.

  Only two ensemble variants are in the grid, deliberately. Every additional variant raises the
  selection-luck bar the eventual winner has to clear, and adding a dozen ensemble configurations
  would reintroduce precisely the problem the ensemble exists to avoid.
- **(2026-09-05) Deflated Sharpe: almost nothing in the sweep is statistically distinguishable from
  selection luck.** `research/validation/deflate_sweep.py` applies Bailey & Lopez de Prado's
  Deflated Sharpe Ratio to the stored runs. The sweep is now 24 variants x 8 instruments = **192
  backtests**, but those are only **15 effective trials** once you account for how correlated they
  are (participation ratio of the equity-curve correlation matrix 15.0, correlation clusters 31 --
  the smaller is used). The best of 15 independent trials would be expected to post **Sharpe 0.97
  annualised by chance alone**. Against that bar:

  | Result | Sharpe | DSR | Verdict |
  | --- | --- | --- | --- |
  | Risk-managed MACD(5,13,5) / Gold Mini | 1.68 | 0.94 | borderline |
  | Bollinger(20, 2.5) / Zinc Mini | 1.61 | 0.93 | borderline |
  | Risk-managed MACD(12,26,9) / Gold Mini | 1.57 | 0.91 | borderline |
  | **MACD(5,13,5) / Gold Mini** (the incumbent pick) | 1.42 | **0.79** | not distinguishable from luck |
  | Regime-Switch(5,13,5+VWAP/EMA) / Aluminium Mini | 1.30 | 0.77 | not distinguishable from luck |
  | Everything else in the top 15 | ≤1.27 | ≤0.71 | not distinguishable from luck |

  **Nothing clears the conventional 0.95 bar.** This is not a sign the bot is broken; it is the
  honest statistical position of a 5-year single-window sweep that reports its own best result, and
  it is exactly why the risk architecture rather than more strategies was the right thing to build.
  Two things follow. First, **the risk layer measurably improves statistical standing, not just the
  headline numbers**: MACD(5,13,5)/Gold Mini goes from DSR 0.79 without stops to 0.94 with them —
  the largest single improvement available anywhere in the sweep. Second, **adding more parameter
  variants now actively costs you**, because every additional trial raises the bar the winner has to
  clear; the grid should shrink, not grow.

  Caveats on the number itself: effective trials is estimated from equity-curve correlations, which
  is a defensible but not unique choice, and DSR assumes the trials are drawn from a common
  distribution. Treat 15 as an order-of-magnitude estimate. Walk-forward validation (train 504 bars
  / test 126 / step 126) is still not built and remains the stronger test.
- **(Found + fixed 2026-09-05) Dhan returns corrupt NICKEL bars, and they had been silently
  poisoning every NICKEL backtest.** 5 of 1,252 daily bars over the 5-year window come back with
  `open=high=low=0.0` alongside a real `close` and a real `volume` — those zeros are missing fields,
  not prices — plus at least one duplicated date. Unfiltered they corrupt everything derived from a
  bar's range: a Donchian channel low of 0, a Bollinger band computed against a 100% "move", an ATR
  inflated by a 1,873-point true range. **How it surfaced:** once ATR-based stops existed, the
  inflated ATR placed a stop at a *negative* price (-0.4), which then "filled" and booked a
  ₹485,199 loss on a single trade — the run reported a **199% max drawdown on a long-only,
  1x-leverage position**, which is arithmetically impossible and was the tell. Before stops existed
  the same bad bars were skewing NICKEL results quietly, with nothing to give them away.
  **Fixed in two places:** `DhanClient.get_historical_ohlc` now drops any bar with a non-positive
  OHLC value or an incoherent high/low, and collapses duplicate timestamps, logging a warning with
  the counts; and `growmore_bot.risk.exits` refuses to place a stop at or below zero regardless of
  what ATR says. Dropped rather than repaired: reconstructing `open=high=low=close` would invent a
  zero-range bar, which quietly deflates ATR and flatters every range-based indicator — five missing
  days in five years is the smaller and more visible distortion. Only NICKEL is affected today
  (checked across all 8 instruments), but the guard is universal since bad prints are a property of
  the feed.
- **(2026-09-05) The risk layer helps most where drawdown was worst, and hurts on low-volatility
  contracts.** A paired comparison of the same strategy on the same instrument, with and without a
  2×ATR initial stop plus a 3×ATR Chandelier trail, across 13 pairs that clear the 15-trade
  guardrail: **better on BOTH Sharpe and max drawdown in 8, worse on both in 3.** The wins are
  large where they land — MACD(5,13,5)/Gold Mini goes from 19.1% max drawdown to **8.8%** with
  Sharpe 1.42 → 1.68 and CAGR slightly up; MACD(12,26,9)/Gold Mini 29.0% → **10.3%** with Sharpe
  0.96 → 1.57; every Nickel pairing collapses from 40–65% drawdowns to 11–15%. The losses are
  consistent and explainable: Aluminium Mini and Zinc Mini both get *worse* on both metrics,
  because 2×ATR on a low-volatility contract sits inside normal noise and converts winners into
  stopped-out losers. MACD(12,26,9)/Silver Mini is a genuine trade-off rather than a win — CAGR
  33.4% → 21.6% but drawdown 36.8% → 22.4%. **Conclusion: the stop multiple wants calibrating per
  instrument, not applying universally at 2×ATR.** That calibration has not been done and is the
  obvious next experiment; until it is, a risk-managed config should only be enabled on an
  instrument where the paired comparison above is favourable.
- **(Found + fixed 2026-09-05) `risk_managed` had no working stop in paper or live trading at all --
  `position_state["risk"]` was never constructed.** `RiskManagedStrategy`'s per-trade stop/trail
  state is designed to round-trip through `position_state["risk"]` every tick (`BacktestEngine`
  does this correctly), but neither `paper/engine.py` nor `live/engine.py` ever built a `"risk"` key
  -- both only passed `{"quantity": ..., "avg_entry_price": ...}`. Any `risk_managed` config running
  in paper or live trading had its computed stop silently reset every single tick: the wrapper always
  saw an empty risk dict, so the initial stop was lost immediately and the trailing stop never
  advanced. Found investigating the account owner's request (below) to place a real broker-side stop
  order -- there was no correct stop price to place one at until this was fixed. Fixed: both engines
  now fetch the open position's `risk_state` into `position_state["risk"]` every tick and persist
  `Signal.risk_state` back onto the position row afterward (new `risk_state` JSONB column, migration
  `0017_risk_state`). Regression tests in both `test_paper_engine.py` and `test_live_trading_engine.py`
  assert the round-trip explicitly.
- **(Fixed 2026-09-05) A real resting SL-M stop order, for `live/engine.py` only.** Previously the
  backtest's stops were optimistic relative to what the bot could execute: the scheduler polls every
  5 minutes, so a live "stop" only fired in software at the next poll's LTP, not at the actual stop
  level -- the backtest's `stop_slippage_ticks` was a deliberate hedge for exactly this gap, not a
  measurement of a real mechanism. Fixed: `DhanOrderClient` gained
  `place_stop_loss_market_order`/`modify_stop_loss_trigger`/`cancel_stop_loss_order` (confirmed
  against the installed `dhanhq==2.2.0` SDK source -- `place_order`'s `order_type=SLM`/
  `trigger_price`, `modify_order`, `cancel_order` all already exist). `LiveTradingEngine` now places a
  real stop at entry (`stop_order_id`/`stop_order_trigger_price` on `LivePosition`), moves it via
  modify whenever the wrapper's trailing stop ratchets, cancels it before any other exit fires (so it
  can't race a strategy-signal sell, a time stop, expiry, end-of-day, or a daily-loss-limit
  auto-close), and `reconcile_pending_orders` now closes the `LivePosition` (with
  `close_reason="broker_stop_loss"`) when the resting order fills between polls -- the actual point
  of the feature. Paper trading has no real broker to place a resting order at, so its stop stays
  software-detected by design; the risk_state fix above means it now at least enforces correctly.
  **UNVERIFIED against a real order**: whether Dhan accepts an SL-M order for MCX_COMM, and the
  `modify_order`/`cancel_order` real response shapes for a plain (non-Super) order, have not been
  confirmed with an actual placed order -- see `docs/pending-actions.md`. Not enabled anywhere yet;
  live trading is still blocked on the pre-existing unverified MCX order-quantity-unit question.
- **The backtest still does not model the live engines' own guards.** `daily_loss_limit`, the
  contract-expiry force-close and the end-of-day flatten for `requires_intraday_flatten` strategies
  all exist in `paper/engine.py` and `live/engine.py` and in none of `backtest/engine.py`. Backtest
  and live are therefore not quite the same system, and the gap widens for any strategy that would
  actually trip one of those guards.
- **(Found + fixed one instance 2026-09-04) Neither disabling NOR deleting a `bot_config` closes its
  open position.** `scheduler/run.py`'s main tick loop only queries `enabled=True` configs -- flip a
  config to disabled (the dashboard's normal toggle) and it's simply never fetched or evaluated
  again, with no quote pulled and no close attempted. The only tick that runs regardless of `enabled`
  is the `pending_auto_close` retry loop, which covers exactly one case (a daily-loss-limit guard that
  already tried and failed to auto-close) -- a plain manual disable never sets that flag, so it isn't
  covered either. Net effect either way (disable OR delete the row): any open position just sits
  there, unrealized P&L frozen at its last tick, until the config is re-enabled (so the strategy can
  eventually produce a natural SELL) or the position is closed out by hand. Found live: deleting
  COPPER `rsi_mean_reversion` and CRUDEOILM `always_flip` from paper trading earlier this session left
  exactly this -- both showed up on the Trade Log permanently "open." Fixed by hand this time (marked
  `closed`, `realized_pnl` set to the last frozen `unrealized_pnl`, an `audit_log` entry explaining
  why) -- **not yet fixed at the process level**. Before disabling or deleting a `bot_config` in the
  future, check for an open position on that (strategy_id, instrument_id) pair first and close it out
  (or build a proper "disable and flatten" action instead of a bare toggle/delete).
- **(Found + fixed 2026-09-04) Second independent review of every strategy/backtest calculation
  found and fixed 6 more real bugs.** A from-scratch re-review of `strategies/`, `backtest/`,
  `research/smallcap_momentum/`, and the dashboard's own indicator solvers, re-deriving every
  formula by hand rather than trusting the surrounding comments. Confirmed and fixed:
  1. **`backtest/run_all.py` -- the worst of the batch: the parameter sweep shared ONE stateful
     strategy instance across every instrument.** `_build_strategy_grid()` returned an
     already-constructed instance per variant, and `main()`'s `for instrument in instruments:` loop
     reused it. Every strategy here is stateful (rolling close deques, seeded EMAs,
     previous-crossing flags, ADX's previous bar), so **every instrument after the first one in the
     sweep began with the previous commodity's price history still loaded** -- Gold Mini's
     ~70,000-level closes sitting in the window as Copper's ~700-level bars arrived, fabricating
     crossings and breakouts across the whole early history and corrupting the very rankings that
     decide which strategy gets real money. Fixed: the grid now returns a `functools.partial`
     FACTORY, and a fresh instance is built per (instrument, variant). **Any multi-instrument
     backtest sweep run before this fix should be re-run** -- results for the first instrument
     processed are valid, everything after it is not. **Done 2026-09-04**: the full 144-run sweep
     was re-run against the real Neon database (old 112 corrupted rows deleted first, not patched in
     place) -- see `docs/backtest-results.md` for the corrected numbers. The most consequential
     change: the previously-live-justifying GOLDM `rsi_mean_reversion` config's CAGR corrected from
     60.8% to 14.6% (still a solid strategy, just no longer the standout it looked like); the overall
     #1 pick corrected from 78.2% to 21.9% CAGR.
  2. **`backtest/metrics.py` -- `cagr_pct` returned a COMPLEX number for a wiped-out run.**
     `(negative) ** (1/years)` doesn't raise in Python, it returns a complex value, which flowed
     straight into `BacktestRun.cagr_pct`. Reachable in practice, not theoretically: the engine
     trades whole leveraged commodity lots against an unleveraged `initial_capital`, so a bad
     variant's equity curve can cross zero. Fixed: an end equity at or below zero is -100%.
  3. **`strategies/vwap_session_bounce.py` -- a live quote with no session VWAP yet silently
     replaced today's CPR gate.** The strategy told a historical `Bar` apart from a live `Quote` by
     whether `vwap` was present -- but `Quote.vwap` is legitimately `None` early in a session (see
     the `average_price: 0` fix above) and a real `Quote` *also* carries high/low/close, so such a
     tick fell into the warm-up branch and recomputed `_current_cpr` from **today's own partial
     session range** instead of yesterday's daily bar. Fixed: a live quote is now identified by
     `ltp` (which no historical `Bar` has), and a VWAP-less tick leaves both the CPR and the
     crossing reference untouched.
  4. **`dashboard/lib/percent-to-signal.ts` -- the "% move to signal" solver for `sma_crossover`
     and `macd_trend` answered the wrong question.** Both solved for a price *appended as a brand-new
     bar* (rolling `oldest_fast`/`oldest_slow` out of the SMA windows; taking one more EMA step from
     the EMAs that already include the live price). That models a day that never happens: the
     scheduler rebuilds the strategy every tick and warms it up from history ending *yesterday*, so
     the next tick sees this exact state with only the live price **replaced**. On a realistic GOLDM
     series the MACD figure understated the true distance by more than 3x. Fixed to the exact
     same-tick solve -- `target = ltp + (slowSma - fastSma) / (1/fast - 1/slow)` for SMA, and for
     MACD `target = ltp + (signalPrev - macd) / (kFast - kSlow)` where the prior signal line is
     recovered exactly from the current one. Both were then verified end-to-end against the real
     Python strategies: a price a hair short of the solved target holds, a hair past it fires
     exactly the expected BUY/SELL, across several parameter sets. (`rsi_mean_reversion`'s solver
     already used the correct model and is unchanged.)
  5. **`dashboard/lib/{percent-to-signal,signal-explain}.ts` -- Donchian reported an unreachable,
     backwards signal.** Only the *transition* into a breakout fires (see the strategy's
     `prev_breakout_state`), but with price already above the channel high the solver returned a
     NEGATIVE "% to BUY" and the prose claimed price still "needed to rise" to break a high it had
     already broken. Fixed: the already-broken side reports no reachable signal, matching how
     `bollinger_reversion` was already handled. The RSI explanation's `<`/`>` threshold checks were
     also tightened to `<=`/`>=` to match the strategy's own boundary.
  6. **`persistence/migrations/env.py` -- Alembic silently disabled every existing logger.**
     `fileConfig()` defaults to `disable_existing_loggers=True`, and this env runs in-process, so
     once the integration tests applied migrations every already-imported `growmore_bot.*` logger
     went dead for the rest of the process -- making five paper-engine logging tests fail, but only
     for someone who actually had Postgres running for the full suite CLAUDE.md mandates. Fixed with
     `disable_existing_loggers=False`. The full suite (325 tests, integration included) is now green.
  Each fix got a TDD regression test (confirmed red before, green after). Reviewed and
  hand-verified as correct with no change needed: all eight strategies' core formulas and crossing
  semantics, the backtest engine's next-bar-open fill discipline and lot-size scaling, Sharpe /
  max-drawdown / win-rate / profit-factor, the paper and live engines' P&L sign and scaling
  (including the live engine's back-solved retroactive fill correction), the scheduler's
  MCX-timezone daily-P&L window and single-day-strategy state reset, MCX seasonal session hours,
  and the smallcap research module's momentum / quality / z-score / portfolio mark-to-market maths.
- **Cross-sectional research backtest rebalances at the same close it ranks on.**
  `research/smallcap_momentum/portfolio_engine.py` computes each rebalance's momentum/quality scores
  from prices through `day`'s close and then buys at that same close. No *future* information is
  used (the entry price is a price already observed), so this is the standard index-rebalance
  simplification rather than lookahead -- but it is mildly optimistic and is deliberately *not* the
  next-bar-open discipline `backtest/engine.py` enforces for the commodity sweep. Also unmodelled
  there: transaction costs/slippage, and a delisted or halted name marked forward at its last known
  close indefinitely (survivorship/stale-price bias). Fine for the relative comparison between
  variants that module exists to make; would need fixing before treating its absolute CAGR as real.
- **`scheduler/run.py` can raise `AttributeError` if a `mode="live"` config is ticked with
  `live_trading_enabled=True` but no `order_client`.** Three call sites use `live_engine.*` where
  `live_engine` is `LiveTradingEngine | None` (mypy flags all three today). Not reachable via
  `start()`, which always constructs an order client when the kill switch is on -- but it is
  reachable by any other caller of `run_all_enabled_configs`, and the failure mode is a crashed
  tick, not a wrong trade. Left as-is deliberately: adding a fallback risks masking a real
  misconfiguration, which CLAUDE.md's non-negotiables forbid.
- **(Found + fixed 2026-09-04) Independent double code review of every strategy algorithm found and
  fixed 5 real bugs.** Given real money is ultimately at stake, two independent subagents each did a
  full from-scratch review of every file in `strategies/` (recomputing formulas and test docstrings
  by hand, not trusting existing comments), cross-checked against each other. Confirmed and fixed:
  1. **`vwap_session_bounce.py` / `dhan_client.py`**: a real `average_price: 0` from Dhan (no trades
     printed yet this session) was parsed as `Quote.vwap = 0.0`, not `None` -- `ltp > 0.0` is always
     true, fabricating a VWAP "crossing" (and a BUY/SELL) the instant real trades start printing.
     Fixed: a falsy/zero `average_price` is now treated the same as genuinely absent.
  2. **`donchian_breakout.py`**: had no crossing-state snapshot at all -- the only real signalling
     strategy without one. Re-signalled BUY/SELL on every single live tick the price stayed outside
     the channel (a fresh strategy instance is rebuilt every 5-minute tick), either freezing
     `unrealized_pnl` (the repeat signal gets rejected by `max_position_size`, which skips
     mark-to-market) or silently pyramiding a position. Fixed with the same
     capture-previous-state-before-crossing pattern every other strategy already uses. Not currently
     used by any `bot_config`, so no live/paper harm occurred -- backtest results are unaffected
     (the backtest engine already no-ops a repeat BUY-while-long/SELL-while-flat).
  3. **`rsi_mean_reversion.py`**: a perfectly flat price window (every diff exactly 0, both
     `avg_gain`/`avg_loss` zero) reported RSI as 0.0 (maximally oversold) instead of the conventional
     neutral 50 -- fabricating a BUY on the very next up-tick. Realistic on MCX: an illiquid
     far-month contract can print an identical settlement close for several sessions.
  4. **`regime_switch.py` + both engines' `_format_debug_state`**: `debug_state()` returns
     `"regime": "trending"|"ranging"` (a string), but the log-formatting helper unconditionally did
     `f"{v:.2f}"` on every non-None value -- `ValueError` the moment ADX became computable, crashing
     the ENTIRE scheduler tick (no try/except around `process_tick`) and silently skipping every
     `bot_config` processed after it in that loop. Not currently reachable (`regime_switch` has no
     `bot_config` row), but a real landmine the moment one is added. Fixed the formatter to render
     any non-numeric value via plain `str()` instead of crashing.
  5. **`vwap_session_bounce.py` / `scheduler/run.py`**: its crossing state (`prev_above_vwap`) was
     persisted and restored across the calendar-day boundary with no reset, even though VWAP and CPR
     are both explicitly single-day concepts. Could fabricate a "crossing" (and a signal) on the very
     first live tick of a new trading day, using yesterday's crossing reference against today's fresh
     VWAP. Fixed: the scheduler now only restores `crossing_state` for a `requires_intraday_flatten`
     strategy when the persisted state's `checked_at` is from the SAME MCX trading day as `now`;
     otherwise it's discarded (correctly reverting to "nothing to cross from yet" on a new day).
     Multi-day strategies (SMA/MACD/RSI/etc.) are unaffected -- their crossing reference is
     deliberately meant to persist across days.
  All five got a TDD regression test (confirmed red before the fix, green after) and were deployed
  to the VPS with a clean verified tick. Everything else reviewed (SMA/MACD/RSI core formulas,
  Bollinger population-stddev, Donchian's no-lookahead window, the Wilder DMI/ADX recurrence and
  hysteresis, the rolling VWAP, engine-level P&L sign/scaling in all three engines, snapshot
  round-trips, state-mutation ordering) was independently hand-verified as correct by both reviewers.
- **(Found + fixed 2026-09-04) Daily-bar strategies (RSI/MACD/SMA/Donchian/Bollinger/regime_switch)
  never reacted to intraday price movement on a live tick.** `paper/engine.py` and `live/engine.py`
  fetched a live `Quote` and passed it straight to `strategy.on_bar(quote, ...)`. Every daily-bar
  strategy reads `bar.close` expecting "today's price so far", but `Quote.close` is Dhan's
  `ohlc.close` field — the **previous trading day's official close**, fixed all session (this is the
  same field `bot_signal_state.prev_close` is deliberately sourced from, for the dashboard's "Today
  ±X%" badge). So every live tick appended the SAME frozen yesterday's-close value to a strategy's
  window regardless of how far the real LTP had moved — RSI/MACD/SMA/etc. only ever updated the
  *next* calendar day once warm-up replayed a genuinely new bar, never intraday. Found live when the
  account owner noticed GOLDM's `rsi`/`avg_gain`/`avg_loss` were bit-for-bit identical across two log
  lines 71 minutes apart despite LTP moving 622 points. No test caught it because every `Quote(...)`
  fixture in `test_paper_engine.py`/`test_live_trading_engine.py` happened to set `close == ltp`.
  **Fixed**: both engines now call `strategy.on_bar(dataclasses.replace(quote, close=quote.ltp),
  ...)` — `high`/`low` are left untouched since Dhan's live `ohlc.high/low` genuinely are today's
  real session values (needed as-is by Donchian/Bollinger/regime_switch), only `close` was stale.
  `_record_signal_state` still reads `prev_close` from the *original*, unwrapped `quote`, so the
  dashboard's "Today %" badge is unaffected. Added a `_SpyStrategy` regression test in both engines'
  test files asserting `on_bar` receives `bar.close == quote.ltp` with `ltp != close` in the fixture
  (the previous fixtures' `ltp == close` had masked this for as long as the bug existed). Deployed to
  the VPS and confirmed live: GOLDM's RSI immediately changed (26.94 → 9.93) on the next real tick.
- **(Found + fixed 2026-09-04) GOLDM's `lot_size` was entered as 100 (raw grams) instead of 10 (quote-
  units per lot), overstating every Gold Mini rupee P&L/notional figure 10x.** MCX Gold Mini is a
  100-gram lot, but the futures price is quoted **per 10 grams** — confirmed independently via web
  search, not just inference. Every engine (`backtest/engine.py`, `paper/engine.py`, `live/engine.py`)
  and the dashboard's notional/P&L math do `price × lot_size` directly, treating `lot_size` as "quote-
  units per lot" — true for every other instrument in the universe (their quote unit happens to equal
  their lot's own unit) but not for GOLDM. Found live when the account owner spotted a GOLDM paper
  position showing ₹1.55 **crore** notional exposure for one Mini lot. **Fixed**: `lot_size=10` in
  `growmore_bot/config.py`'s `DEFAULT_COMMODITY_UNIVERSE`, backfilled on the real `instruments` row
  (`update instruments set lot_size = 10 where symbol = 'GOLDM'`, logged to `audit_log` as
  `instrument_lot_size_corrected`), and the 14 pre-existing GOLDM `backtest_runs` (computed with the
  buggy value) were deleted and replaced with freshly re-run, corrected results — e.g.
  `macd_trend fast5-slow13-sig5`: CAGR 78.17%→21.87%, Sharpe 1.56→1.38, max drawdown 37.35%→24.37%.
  `profit_factor`/`win_rate_pct` were unaffected (ratios of a run's own trade outcomes, scale-
  invariant) but CAGR/Sharpe/max-drawdown for every GOLDM strategy were previously significantly
  inflated — this may be worth revisiting for GOLDM's live-mode `rsi_mean_reversion` config, whose
  real backtested CAGR is materially lower than what justified enabling it. Existing GOLDM paper/live
  positions' `unrealized_pnl` self-corrected on the very next tick (recomputed fresh each time, not
  stored as an incremental delta); historical `realized_pnl` on already-closed GOLDM paper orders
  remains permanently 10x-overstated in the audit trail (not rewritten, matching this project's
  practice of not editing historical records).
- **(Found + worked around 2026-09-04) `${JSON.stringify(x)}::jsonb` silently double-encodes an
  EMPTY object via the `postgres` npm package.** Creating the `vwap_session_bounce` strategies row
  with `params: {}` using this project's usual pattern (`${JSON.stringify({})}::jsonb`) stored a
  JSONB **string** `"{}"` instead of a JSONB **object** `{}` (confirmed via `jsonb_typeof`) — broke
  the live scheduler with `TypeError: ... argument after ** must be a mapping, not str` for one real
  tick before being caught and fixed (`update ... set params = '{}'::jsonb`, a literal rather than a
  bound parameter). Every other use of this pattern in `dashboard/lib/db.ts` passes a non-empty
  object (always includes at least `bot_config_id`) and is unaffected — this only bites a genuinely
  empty `{}`. Not fixed at the pattern level (would touch unrelated working code); just noting the
  trap for the next empty-object JSONB insert.
- **(2026-09-04) Two new Gold Mini strategies added; one tested with a real negative result, one
  untested in production.** `regime_switch` (ADX-gated MACD/RSI or MACD/VWAP+EMA) was backtested
  against real 5-year Gold Mini data and **underperformed the standalone strategies it's built from
  on every metric** — see `docs/goldmini-regime-switch-results.md` for the full honest writeup.
  **Not enabled for paper or live trading.** `vwap_session_bounce` (live CPR+VWAP intraday bounce) is
  wired into `strategy_builders` and unit-tested, but has **no backtest at all by design** (its core
  signal, Dhan's live session VWAP, doesn't exist in historical bars) and has not yet been added as a
  `bot_config` row — the plan called for validating it via real paper trading, which hasn't started
  yet. Both strategies introduced `Strategy.requires_intraday_flatten` (default `False`) and a new
  scheduler branch (`is_near_session_close` + `force_close_end_of_day` on both engines) that force-
  flattens a position near the daily MCX close for any strategy that sets it — exercised by unit
  tests but not yet by a real intraday position (none has been opened under this flag yet).
- **(Incident, fixed 2026-09-04) Generating a Dhan access token from a second machine invalidates
  the VPS's active live-trading token.** While verifying Dhan's NSE equity data support for the
  small-cap research (a one-off local script on the account owner's Mac), a fresh access token was
  generated via the headless PIN+TOTP flow. Dhan appears to allow only one active access token per
  account — this immediately invalidated the token the VPS's `growmore-bot.service` was actively
  using for real trading. The live bot then failed every 5-minute tick for roughly 2 hours with
  `DH-906 Invalid Token`, visible in `bot.log`. **No incorrect trades resulted** — the failure was in
  fetching quotes (`get_quote`/`get_historical_ohlc`), not order placement, and the daily-loss-limit
  guard (which reads `cumulative_daily_pnl` from already-stored orders, not a live quote) remained
  functional throughout; the real ALUMINI position simply went unmonitored/unmarked-to-market for
  that window. Fixed by generating one more token and writing it to **both** the VPS's and the local
  repo-root `.env.local` (`growmore_bot.broker.token_refresh.write_access_token_to_env_file`), then
  restarting the service — confirmed recovered via real ticks in `bot.log` afterward. **Going
  forward: never generate a fresh Dhan access token from any machine other than the one currently
  live-trading**, even for a read-only research/verification call — reuse the existing token (it's
  already in the repo-root `.env.local`) or coordinate the refresh with whatever is actively trading.
- **(Fixed 2026-09-04) Systemic repeated-signal bug across all crossing-based strategies.** The
  scheduler rebuilds a fresh strategy instance every tick and warms it up from history ending
  yesterday (`_warm_up_strategy`) — so a strategy's "previous value" for crossing/threshold-recovery
  detection was always yesterday's close, not the last time it was actually checked. A signal meant
  to fire exactly once at a real crossing instead re-fired on every tick for the rest of the day the
  live value stayed past the threshold. Found via the real Aluminium Mini live position's unrealized
  P&L staying stuck at 0 — the strategy kept re-signalling BUY (correctly rejected by the
  `max_position_size=1` guard, but that rejection path skips mark-to-market) instead of reporting
  HOLD. This was a real risk for any config with `max_position_size > 1` — a repeated erroneous BUY
  would have placed additional real orders each tick, not just been rejected. Fixed with
  `Strategy.get_state_snapshot()`/`load_state_snapshot()` — `macd_trend`, `rsi_mean_reversion`,
  `sma_crossover`, and `bollinger_reversion` now persist/restore their crossing reference via a new
  `bot_signal_state.crossing_state` column, restored by the scheduler right after warm-up.
  `donchian_breakout` deliberately left alone — not currently used by any config, and has a
  genuinely different (non-crossing, threshold-breach) design that shouldn't be changed without its
  own separate review (changing it would also retroactively alter the semantics behind the already-
  published `docs/backtest-results.md` sweep, which used the old behavior).
- **(Investigated + throttled 2026-09-04) Repeated `max_position_size_rejected` audit_log entries
  are a real signal, not a recurrence of the fixed repeated-signal bug above.** Noticed via the
  dashboard's audit log showing the same rejection for ALUMINI (live) and COPPER (paper) roughly
  every 5-minute tick for ~50 minutes around MCX's 2026-09-04 market open. Root-caused by checking
  each strategy's own crossing state (`bot_signal_state.crossing_state`) directly against the real
  DB: both were genuinely, repeatedly crossing their threshold (MACD/signal spread, RSI vs. 30) due
  to real intraday price noise right at the boundary — each rejection really was a fresh BUY signal,
  correctly rejected because a position was already open. `RsiMeanReversionStrategy`/`MacdTrendStrategy`
  don't consult `position_state` at all (by design — signal generation is separate from position
  sizing, per `Strategy.on_bar`'s contract), so they have no way to know not to re-signal BUY while
  already holding; the `max_position_size` guard is exactly the intended safety net for this. Not a
  bug, but genuinely low marginal audit-log value once already recorded once recently — throttled to
  one audit_log write per 30 minutes per config via a new
  `bot_signal_state.last_max_position_rejection_logged_at` column (`bot.log`'s own warning is
  unaffected, still written every single tick).
- **(2026-09-04) First real live-trading attempt: rejected safely, root-caused, fixed.** With the
  account owner's explicit go-ahead, Aluminium Mini's MACD (12,26,9) config was switched to
  `mode="live"` and `LIVE_TRADING_ENABLED=true` was set on the VPS. The very next tick placed a real
  BUY order attempt — Dhan rejected it with `DH-905 Invalid IP`. **Root cause**: the droplet has real
  IPv6 connectivity, and `api.dhan.co` resolves to both IPv4 and IPv6 — the actual outbound HTTPS
  connection went out over IPv6, which doesn't match the IPv4 (`139.59.72.81`) registered with Dhan.
  **Fixed** by disabling IPv6 system-wide on the droplet (`/etc/sysctl.d/99-disable-ipv6.conf`),
  verified afterward with `curl -w 'local_ip=%{local_ip}'` against Dhan's own API host showing the
  correct registered IP. A second, more serious bug surfaced by the same failure: the order client's
  own `live_order_failed` audit_log entry was being silently discarded, because nothing caught the
  exception before it reached `session_scope()`'s rollback-on-any-exception handler — the one moment
  an audit trail matters most (a failed real order) left no trace at all. Fixed by catching
  order-placement failures at each call site in `live/engine.py` instead of letting them propagate.
  **No real order was ever actually placed** — Dhan rejected it before any money moved.
  `LIVE_TRADING_ENABLED` was immediately set back to `false` once the failure was seen, pending both
  fixes; re-arming is a deliberate separate step, not automatic.
- **(Built 2026-09-04, still OFF) Real order placement now exists but is gated behind two
  independent switches, both required.** `growmore_bot/broker/dhan_order_client.py` is the only
  module allowed to call Dhan's Order API (schema verified against the installed `dhanhq` SDK's own
  source — `exchangeSegment="MCX_COMM"`, `productType="MARGIN"` for carry-forward, `orderType=
  "MARKET"` — not guessed, and not the conflicting "MCX_FO" a scraped doc page claimed). Every call
  requires `Settings().live_trading_enabled` (env `LIVE_TRADING_ENABLED`, off by default) AND the
  specific `bot_config.mode == "live"` (new column, defaults `"paper"` for every existing row, no
  dashboard UI to change it — must be set directly in the database, deliberately, so it's never an
  accidental click). `growmore_bot/live/engine.py` mirrors `PaperTradingEngine`'s interface and risk
  guards (max_position_size, daily_loss_limit, pre-expiry close-out) exactly, persisting to new
  `live_positions`/`live_orders` tables (never `paper_positions`/`paper_orders`, so real and
  simulated data can never mix). Full TDD coverage, all mocked (no real order was ever placed while
  building this). Known gaps, left as gaps rather than silently papered over:
  1. **(Fixed 2026-09-04) Fill reconciliation now corrects position-level P&L too, not just the
     order's own record.** A `MARKET` order's placement response only carries an order ID and an
     initial status (e.g. `"TRANSIT"`), not a confirmed fill price.
     `LiveTradingEngine.reconcile_pending_orders()` polls Dhan's `get_order_by_id` once per tick for
     every `live_orders` row still in a non-terminal status and corrects that row's
     `order_status`/`fill_price` to Dhan's real values. When the real fill price differs from the
     approximate live-quote LTP used at placement, `_retroactively_correct_position` now ripples that
     correction into the position too: for a SELL, the order's own `pnl`/old fill price let the
     avg_entry_price used at that sale be back-solved exactly, so the corrected pnl (and the delta
     applied to `realized_pnl`) doesn't need to know the position's *current* avg_entry_price, which
     may have moved since. For a BUY, avg_entry_price is only recomputed when it's safe to do exactly
     — the position is still open and has never had a sell against it — as the quantity-weighted
     average of every buy order's (corrected) fill price; once any sell has happened, exactly
     correcting cost basis needs per-lot tracking this engine doesn't have, so it's logged for manual
     review instead of guessed at. Also worth noting: unlike `place_market_order`'s request schema
     (verified against the `dhanhq` SDK's own source), the response field names this relies on
     (`orderStatus`, `averageTradedPrice`) are only documented, not independently verified against a
     live response beyond the one real order placed so far.
  2. **(Fixed 2026-09-04) Daily-loss-limit trip on a live config now attempts to auto-close the real
     position, and retries with backoff if that fails.** `LiveTradingEngine._trip_daily_loss_guard`
     places a real closing SELL for whatever's open before disabling the config, and audit-logs
     `auto_close_attempted`/`auto_close_succeeded`/`auto_close_pnl` either way. If the closing order
     itself fails (caught, never raised), the config is marked `pending_auto_close` with a geometric
     backoff schedule (5, 10, 20, 40... minutes, capped at 60, never gives up on its own) —
     `run_all_enabled_configs` retries it every tick via `LiveTradingEngine.retry_pending_auto_close`,
     DELIBERATELY outside the `enabled=True` filter that gates everything else, since the whole point
     is retrying a position close for a config that's already disabled. A successful retry never
     re-enables the config for fresh trades, only flattens the position; each attempt (success or
     failure) writes its own `live_auto_close_retry_succeeded`/`live_auto_close_retry_failed` audit
     entry. Applied the original (non-retrying) auto-close fix to `PaperTradingEngine` too, for
     consistency/realism, even though a failed *simulated* close carries no real risk.
  3. **Dashboard doesn't read `live_positions`/`live_orders` or expose `bot_config.mode` at all
     yet** — not urgent while `live_trading_enabled` stays False, but needed before this is ever
     actually used, so real activity is visible somewhere.
  Still blocked on the items below (static IP) before this could ever safely be turned on for real —
  see `docs/pending-actions.md` for the activation checklist. The 2FA/OAuth session-requirements
  question is now resolved (see below, under SEBI Algo-ID) — not a blocker.
- **(Done 2026-09-04) Bot moved off the local machine, onto a DigitalOcean VPS** — droplet
  `growmore-bot` (1 vCPU/1GB, Ubuntu 24.04, Bangalore `blr1`, public IP `139.59.72.81`), hardened
  (key-only SSH as a non-root `growmore` user, root login + password auth disabled, `ufw` allowing
  only SSH, `fail2ban` enabled), running the bot as a systemd service (`growmore-bot.service`,
  auto-restarts on crash/reboot). The laptop's own bot process was stopped at the same time — running
  two instances against the shared Neon database simultaneously would double up paper trades. Still
  paper-trading only; nothing about trading behavior changed, this only solves the hosting/IP problem.
  **(Done 2026-09-04) IP registered with Dhan** — the droplet's IP (`139.59.72.81`) is now whitelisted
  on the Dhan account, so the static-IP requirement is actually satisfied, not just theoretically
  possible. Locked for 7 days from registration (~2026-09-11) per Dhan's own policy.
- **No SEBI Algo-ID handling.** Not needed for paper trading (we never call the Order API). Verified
  2026-09-04 that this is a much smaller lift than first assumed: SEBI's framework exempts self-built
  "White Box" strategies (logic transparent to the owner, not sold to others — this bot qualifies)
  from formal exchange strategy registration as long as order rate stays under **10 orders/second per
  exchange per client** — this bot polls every 5 minutes, nowhere near that threshold, so no
  multi-week exchange-approval process is expected to apply before live trading. What DOES still
  apply regardless of the exemption: a static IP whitelisted with the broker (see the item above —
  same underlying requirement, now confirmed to be part of this SEBI framework too, not just a
  Dhan-specific policy), 2FA on every session, and broker-side order tagging/audit logging (the bot
  already keeps its own `audit_log` table and `bot.log`, which should cover the "keep audit-ready
  logs" expectation). **(Resolved 2026-09-04)** the 2FA/OAuth question specifically, checked directly
  against Dhan's own authentication docs: Dhan documents TWO sanctioned ways to get an access token —
  a full OAuth App ID/Secret browser-consent flow (needs a public HTTPS redirect URL we don't have),
  or **programmatic generation via client ID + PIN + a live TOTP code** — exactly what
  `token_refresh.py` already does. This isn't a workaround around Dhan's 2FA requirement, it IS Dhan's
  own documented headless 2FA mechanism (the PIN+TOTP pair together constitute the two factors). Dhan's
  docs also confirm there's no additional per-API-call session requirement beyond the 24h token
  itself, and static IP whitelisting only gates Order Placement APIs specifically (not Data APIs) —
  matches everything already built. One new operational detail worth remembering: once static IPs are
  registered with Dhan, **they can't be changed for 7 days** — worth confirming the VPS provider/IP
  before registering it, not after. **(Fixed 2026-09-04)** the "automatic session reset before each
  trading day" expectation specifically: `scheduler/run.py`'s `start()` now forces a fresh Dhan
  session via `token_refresh.refresh_if_needed(force=True)` once per IST calendar day
  (`token_refresh.is_new_trading_day`), independent of how much validity the current token has left —
  previously it only ever refreshed reactively, within 2 hours of expiry.
- **Dhan sandbox not used for market data.** Confirmed via Dhan docs that sandbox fills all orders
  at a fixed ₹100 and does not provide real quotes — unsuitable for realistic paper-trade
  simulation. We use the production Data API (read-only) for real prices instead; see
  `docs/pending-actions.md` for the credential this requires.
- **Single point of failure.** The bot is a single process with no failover/redundancy. If it
  crashes mid-session, in-flight paper positions are whatever was last persisted — no reconciliation
  logic exists yet.
- **REST polling, not WebSocket.** Live quotes are fetched via polling (default ~5min) rather than
  Dhan's WebSocket feed. Sufficient for the target "not HFT" cadence; revisit if strategies need
  finer-grained data.
- **Backtests use REST-fetched historical data with no local caching layer yet** beyond what's
  persisted per run — repeated backtests re-fetch from Dhan. Acceptable at current data volumes
  (2-4 instruments); revisit if this expands.
- **No reconciliation between paper positions and any real broker state** — by design, since no
  real orders are placed, but this means the moment live trading is introduced, a reconciliation
  layer must be built from scratch.
- **(Fixed 2026-09-03, but worth remembering) A fresh strategy instance used to be built every
  scheduler tick with zero warm-up.** `run_all_enabled_configs` deliberately keeps the scheduler
  stateless — position state comes from `paper_positions`, not memory — but that meant every
  strategy's indicator state (fast/slow EMAs, RSI averages, Donchian channel, etc.) was also
  rebuilt from nothing every 5 minutes and fed exactly one live price before being discarded. Since
  every one of the 5 strategies needs multiple bars of history before it can even compute a value
  (MACD needs 13+), **the bot could have run indefinitely — sleep or no sleep — and never generated
  a single real trade signal.** Found while running it for real for the first time. Fixed by
  `_warm_up_strategy` (`bot/growmore_bot/scheduler/run.py`): each tick now fetches ~150 days of
  real historical daily bars and replays them into the fresh strategy before evaluating the live
  quote as today's still-forming bar. This also means the bot's readiness is independent of process
  uptime — a restart after any length of sleep warms up identically from real history.
  **Known follow-up, not yet done**: this refetches ~150 days of daily bars from Dhan on every tick
  for every enabled config — correct, but wasteful (the historical portion barely changes
  intraday). Caching per calendar day would cut this dramatically; not needed at today's scale
  (one bot_config, ticks every 5 minutes), worth doing before enabling several strategies at once.
- **Preview deployments share the same Neon database as production, and it now holds real data.**
  The Vercel↔Neon marketplace integration was installed without enabling "create a branch per
  preview deployment," so `DATABASE_URL` resolves to the same database for
  Development/Preview/Production. This stopped being a purely theoretical risk on 2026-09-03: the
  real 5-year backtest run (6 backtest_runs, 184 backtest_trades, 6,754 equity_curve_points) is now
  persisted in that one shared database. A preview deploy that writes test data, or a migration
  applied against the wrong environment, can now corrupt or delete real results. Fix — enable
  per-branch Neon databases — before running more backtests or enabling paper trading.
- **Historical data actually does go back the full 5 years Dhan advertises**, per the current
  front-month contract's security ID (verified 2026-09-03: 1,263 daily bars, 2021-09 to 2026-09,
  for Gold Mini's security ID `569003`) — an earlier note here wrongly concluded it was capped at
  ~1 year, from only having tested a 365-day request. The large single-day moves that initially
  looked like a possible data-splice artifact (checked 2026-09-03) turned out to be real, confirmed
  market events (the Jan 30, 2026 gold/silver crash and the Apr 2026 oil crash) — not a data quality
  issue. `default_commodity_universe` in `bot/growmore_bot/config.py` holds real security IDs for the
  current front-month contracts (looked up 2026-09-03) instead of placeholders; these **will need
  updating at each contract roll** regardless of how far back history goes.
- **Market-hours gating (`bot/growmore_bot/scheduler/market_hours.py`) is close to the real MCX
  calendar but not complete.** Weekday + a holiday list + a seasonal close-time shift are now
  handled; two gaps remain (#3 below is fixed, #4 is not):
  1. **(Fixed 2026-09-03) No MCX holiday calendar.** `MCX_HOLIDAYS_2026` now hardcodes the 5
     unambiguous full-closure 2026 holidays (New Year's Day, Republic Day, Good Friday, Gandhi
     Jayanti, Christmas), sourced from Groww's MCX 2026 holiday list, checked 2026-09-03. Deliberately
     does NOT include partial-session holidays (e.g. Holi, Ganesh Chaturthi — some sources describe
     these as "morning closed, evening open," but the exact session boundaries aren't clearly
     documented, and encoding them wrong risks blocking real trading hours, which is worse than the
     status quo of polling needlessly on those days — harmless, since there's no real data moving on
     Dhan's side either). **This list needs a fresh lookup every year** — same maintenance pattern as
     `config.DEFAULT_COMMODITY_UNIVERSE`'s contract expiries — and Dhan's API was never checked for
     a native exchange-holiday endpoint that might avoid hand-maintaining this.
  2. **(Fixed 2026-09-03) The 11:30 PM close didn't account for MCX's seasonal shift to 11:55 PM
     IST.** Now computed per-year directly from the real rule (verified against ICICI Direct's
     coverage of the 2026-03-09 change): MCX closes non-agri commodities at 23:30 IST while the US
     observes daylight saving time (2nd Sunday of March through the day before the 1st Sunday of
     November), 23:55 IST the rest of the year — purely to preserve the same overlap window with US
     markets. `_nth_sunday_of_month()` computes the boundary dates from the DST rule itself, not a
     hardcoded per-year date, so this doesn't need annual maintenance the way the holiday list does.
  3. **(Fixed 2026-09-03) No handling of a contract's last trading day / delivery risk.**
     `is_market_open()` still doesn't know about `contract_expiry`, but this no longer matters for
     the risk it actually posed: `growmore_bot/scheduler/contract_rollover.py` now computes a
     close-out cutoff per instrument (verified against Dhan's real Risk Management Policy — bullion
     gets force-squared-off ~8 trading days before expiry, base metals ~6, both well ahead of Dhan's
     own forced square-off; Crude Oil Mini is cash-settled and gets no forced close-out, matching
     Dhan's own behavior) and `run_all_enabled_configs` force-closes any open position and skips
     strategy evaluation entirely (no fresh entries) for a config past that cutoff. **(Fixed
     2026-09-04) Rolling to the next contract month is now automatic**, attempted on every tick a
     config is past its cutoff: `growmore_bot/broker/instrument_master.py` downloads Dhan's real
     public instrument master CSV (schema confirmed against a live file 2026-09-04) and
     `contract_rollover.roll_to_next_contract()` finds the immediate next MCX FUTCOM contract for
     that symbol, **validates it with a real live quote request before committing anything**, and
     only then updates that one `instruments` row's `security_id`/`contract_expiry` in place (writing
     an audit_log `contract_rolled` entry) — `bot_config` keeps the same `instrument_id` FK
     throughout, so existing enabled configs resume trading automatically, no other change needed.
     Refuses to guess and falls back to the pre-existing manual process (someone looks up the next
     `security_id` by hand and updates the row) whenever the match is ambiguous, the fetch fails, or
     the candidate's quote looks implausible — logged as a warning either way. Known minor
     inefficiency: refetches the ~200k-line CSV every tick while stuck in this fallback state, which
     only happens during the few days between hitting cutoff and a successful roll — not worth
     caching given how rarely and briefly this occurs.
  4. **No handling of MCX special/shortened sessions** (e.g. Muhurat trading, circular-announced
     early closures for specific events) — these aren't predictable from a weekday+holiday-list
     check alone and would need to be sourced from MCX's own circulars if this ever matters for a
     specific date.
  None of this affects backtesting (Dhan's historical data already only contains real trading days),
  only the live scheduler's day/time gating. #1, #2, and #3 are now fixed; #4 remains and is lower
  priority (rare, and mostly relevant once real order placement exists).
- **(Fixed 2026-09-03) `unrealized_pnl` was never marked to market for open
  positions.** `PaperTradingEngine._handle_buy` wrote `unrealized_pnl=0` once
  at position open and `_handle_sell` reset it to `0` again on close
  (`bot/growmore_bot/paper/engine.py`), but nothing ever recomputed it in
  between -- so every open position showed ₹0.00 unrealized P&L on the
  dashboard no matter how far the real price had moved, while realized P&L
  (only ever written on a closing sell fill) was correct. Found via the
  dashboard looking visibly wrong while an `always_flip` demo position was
  open. Fixed by marking the open position to market against the tick's real
  quote on every HOLD (the common case, previously not touched at all), and
  by recomputing it after any partial buy/sell that changes quantity or
  average entry price (`PaperTradingEngine._mark_to_market`).
- **(Resolved 2026-09-04 by decision, not by fixing production) Production dashboard has no access
  control, and that's no longer being worked around — `main` is just never promoted.** The dashboard
  shows trading data and has a real write path (enable/disable strategies, edit risk limits via
  `bot_config`). Vercel Authentication (SSO) protects Preview deployments on the Hobby plan (confirmed
  via a real incognito-mode test — an unauthenticated request gets challenged); extending that same
  protection to Production requires a paid Vercel plan — attempted via API and confirmed blocked:
  `"Vercel Authentication is not available on your plan for production deployments"` (the
  `beautifulforce` team is on Hobby). Rather than upgrade to Pro for this, the decision (2026-09-04) is
  to keep the dashboard permanently on the `live` branch's Preview URL — stable across pushes, already
  protected by the same Vercel Authentication — and never promote to `main`/production at all. Access
  is gated by `beautifulforce` Vercel team membership; see `docs/pending-actions.md` to confirm who's
  on it. `CLAUDE.md`'s "production promotion requires explicit confirmation every time" rule stays in
  force as a backstop regardless.
