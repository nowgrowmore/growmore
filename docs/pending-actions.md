# Pending Actions (for the account owner)

Plain-language list of things only you can do or decide. Updated as the project progresses.

## Decisions waiting on you — added 2026-09-05 after out-of-sample validation

- [ ] **Decide whether the Gold Mini paper config is worth running.** The first out-of-sample
  test says buying and holding one Gold Mini lot would have returned **161%** over the test
  window while the strategy returned **109%**, at almost the same Sharpe (1.99 vs 2.08). The
  strategy's real contribution is a much smaller worst loss — 8.1% drawdown against 18.6%. That
  is a genuine benefit, but it is risk reduction, not extra return, and it should be your choice
  whether it is worth the effort and the execution risk. Detail in
  `docs/walk-forward-results.md`.
- [ ] **Consider switching the Silver Mini work to the risk-managed ensemble with a volatility
  filter.** It is the only idea tested that improved both bullion contracts out-of-sample, and on
  Silver Mini it is the strongest result anywhere in the project (OOS Sharpe 2.49 vs
  buy-and-hold 1.53). No config exists for it yet and I have not created one — enabling a new
  paper config is your call. Detail in `docs/phase4-oos-results.md`.
- [ ] **Do not enable anything on Lead Mini.** Out-of-sample it returns −29.3% at Sharpe −3.29
  on the fixed variant and −21.1% on the incumbent, against a buy-and-hold that did roughly
  nothing. Only two folds of history, so this is weak evidence, but it all points one way.
- [ ] **Know that roughly a third of Gold Mini's historical return is rupee depreciation, not
  gold.** USD/INR went 72.98 → 95.38 over the backtest window (+5.51%/yr). If you size Gold Mini
  on its headline CAGR you are implicitly betting the rupee keeps weakening at that rate. Detail
  in `docs/currency-decomposition-results.md`.
- [ ] **`docs/backtest-results.md` is superseded** and now carries a warning banner. Do not
  quote its numbers; the `risk_managed` rows were produced with a stop bug and the DSR column
  ranks Gold Mini and Silver Mini backwards.

## Blocking for real-price paper trading

- [x] Dhan Individual account opened, Commodity segment active.
- [x] **Production API key generated** (app "growmorelive", App ID `f466f1ff`), TOTP enabled on the
  account (used for headless daily access-token refresh via Dhan's `generateAccessToken` endpoint —
  avoids the App ID/Secret consent flow, which needs a public HTTPS redirect URL we don't have).
- [x] **Data API subscription active** (₹499+GST/month, auto-debited from the Dhan trading-account
  ledger balance every 30 days).
- [x] Real connectivity verified 2026-09-03: live quotes and 1-year daily historical data confirmed
  working for Gold Mini, Silver Mini, and Crude Oil Mini (Natural Gas dropped from the default
  universe per your call). Found and fixed a real bug along the way: Dhan's quote endpoint needs
  security IDs as integers, not strings (`bot/growmore_bot/broker/dhan_client.py`).
- [x] **Fund the Dhan account ledger balance** — done (2026-09-04). Covers the ₹499+GST/month Data
  API renewal; also the real capital that would back any live order once that's ever en/abled.
- [ ] Confirm you're comfortable with the production key having order-placement capability at the
  API level even though our bot will never call those endpoints (Dhan doesn't offer a data-only
  key). Access is scoped by what our code calls, not by the key itself.
- [x] **Automatic daily token refresh fully working**, verified end-to-end 2026-09-03: `DHAN_PIN`
  and `DHAN_TOTP_SECRET` are set in the repo-root `.env.local`, and a real call to Dhan's headless
  `generateAccessToken` endpoint succeeded (a fresh 24h token was issued). The running bot
  (`python -m growmore_bot.main`) now refreshes itself automatically whenever the token is within
  2 hours of expiring — no more manual daily regeneration. (One early test attempt failed with
  "Invalid TOTP" — turned out to be a one-off timing fluke, not a wrong secret or a bug; confirmed
  by comparing a locally-generated code against the authenticator app in real time before retrying.)



## Before enabling anything beyond paper trading

- [ ] Decide per-strategy virtual capital and risk limits (defaults are placeholders in `bot/growmore_bot/config.py` — currently ₹5,00,000 per strategy, review before relying on the numbers).
- [x] Full strategy/parameter sweep completed and **persisted to the real Neon database** — re-run
  2026-09-04 after fixing the cross-instrument-contamination and frozen-live-indicator bugs (the
  original 2026-09-03 numbers below this line were wrong; see `docs/technical-debt.md`): 144 backtest
  runs (6 strategy families × parameter variants × 8 commodities) — see
  **[docs/backtest-results.md](backtest-results.md)** for the corrected ranked top 5 and full caveats
  before acting on any of it (multiple-comparisons risk, no out-of-sample validation yet,
  position-sizing isn't margin-normalized across commodities). Current standout: **MACD Trend
  (5,13,5) + Gold Mini** (CAGR 21.9%, Sharpe 1.38, 91 trades) — corrected down from a previously
  reported 78.2% CAGR / Sharpe 1.56.
- [x] **3** `bot_config` **pairs enabled for paper trading** (2026-09-03), ₹2,50,000 virtual capital /
  1 lot max / ₹15,000 daily loss limit each: MACD (5,13,5) + Gold Mini (the top backtest pick),
  RSI Mean-Reversion (7, 30/70) + Copper, and MACD (12,26,9) + Aluminium Mini — the latter two
  chosen because real live data showed them genuinely close to a signal (RSI at 26 vs. a 30
  threshold; MACD/signal gap of ~0.2), specifically to see different strategy behaviors (a
  mean-reversion strategy vs. two different MACD parameterizations) play out for real, not just in
  backtest. All three ticking correctly as of this check.
- [x] **4th** `bot_config` **added (2026-09-04)**: VWAP+CPR Session-Bounce + Gold Mini, paper mode,
  same ₹2,50,000/1 lot/₹15,000 risk limits. Unlike the others, this one has **no backtest at all by
  design** (see `docs/goldmini-regime-switch-results.md`) — it trades off Dhan's live intraday
  session VWAP, which doesn't exist in historical data, so it's being validated by real paper trading
  instead. Confirmed ticking correctly. A related idea (an ADX-gated regime-switch between MACD and
  RSI/VWAP+EMA) WAS backtested on real 5-year Gold Mini data and came back a clear negative result —
  not enabled anywhere.
- [ ] Decide whether to invest in a continuous/rolled futures series (splicing consecutive expired
  contract-months together) — turned out not to be needed: real per-contract history already goes
  back the full 5 years Dhan advertises (confirmed 2026-09-03), so this is no longer a blocker.
- [x] **Contract rollover is now automatic** (2026-09-04) — as of 2026-09-03 the 8 configured
  commodities' current-contract expiries were: Crude Oil Mini 2026-09-21, Nickel 2026-09-16,
  Copper/Zinc Mini/Aluminium Mini/Lead Mini 2026-09-30, Gold Mini 2026-10-05, Silver Mini 2026-11-30.
  Each will force-close any open paper position and stop taking new trades roughly 6 (base
  metals)/8 (bullion) trading days before its own expiry (Crude Oil Mini is cash-settled, never
  force-closed), then automatically roll itself to the next contract month — no manual step needed
  in the normal case. **Only worth checking in on** if `bot.log` shows repeated "Automatic contract
  rollover attempt failed" warnings for an instrument (falls back to the old manual process: look up
  the next front-month `security_id` from Dhan's instrument master and update that instrument's
  `security_id`/`contract_expiry` directly). Nickel and Crude Oil Mini hit their windows first,
  mid-to-late September 2026 — worth a quick log check around then just to confirm the automatic
  path actually worked, since it's new and unproven against a real live rollover yet.



## Before any real (live) order placement — not in scope yet

- [x] **Real order-placement code path built** (2026-09-04) — `dhan_order_client.py` +
  `live/engine.py` + `bot_config.mode`, fully tested, off end to end.
- [x] **Order-level + position-level fill reconciliation, and auto-close-with-retry on a tripped
  daily loss limit** (added 2026-09-04, position-level correction + retry added 2026-09-04) — see
  `docs/technical-debt.md` for exactly what these do. A failed auto-close no longer just sits there:
  it retries automatically with backoff until it succeeds.
- [x] **GOLDM RSI Mean-Reversion (7, 30/70) prepped for live** (2026-09-04) — `mode` set to `'live'`
  but left **disabled**, specifically so it doesn't start placing real orders on its own (live
  trading is already globally armed for ALUMINI). It now shows up filtered to "Live" on the
  Strategies page, ready to flip on with the existing enable/disable toggle whenever you decide to.
- [ ] **How to actually turn this on, when the items below are ready:** (1) set `LIVE_TRADING_ENABLED
  =true`in the bot's`.env.local`, (2) directly update the specific` bot_config`row(s) you want live to`mode = 'live'` in the database (no dashboard UI for this, deliberately) — everything else (which
  strategy, which instrument, risk limits) stays exactly as already configured for paper trading.
  Both switches are independent; flipping only one does nothing. Ask the agent to do this when you're
  ready — don't do it via raw SQL yourself without walking through the current state of the items
  below first.
- [x] **VPS provisioned and the bot moved onto it** (2026-09-04) — DigitalOcean droplet
  `growmore-bot` (Bangalore, IP `139.59.72.81`), hardened, running as a systemd service. Verified
  ticking correctly as the sole instance (the laptop's copy was stopped to avoid double-trading
  against the shared database).
- [x] **Static IP (**`139.59.72.81`**) registered with Dhan** (2026-09-04, Profile → Get Trading & Data
  APIs → Add IP on web.dhan.co). Dhan's real static-IP requirement for Order Placement APIs is now
  actually satisfied, not just "the VPS exists." **Locked for 7 days from today** — don't try to
  change it before ~2026-09-11 even if the VPS needs to move.
- [ ] SSH access to the droplet is at `ssh -i ~/.ssh/growmore_vps growmore@139.59.72.81` (key-only,
  root login disabled). Ask the agent for `growmore-bot.service` status/logs/restart commands
  whenever needed — no need to remember `systemctl` syntax yourself.
- [ ] **SEBI Algo-ID — smaller lift than originally thought, verified 2026-09-04.** This bot's order
  rate (polls every 5 minutes) is nowhere near the 10-orders/second-per-exchange threshold that
  triggers formal exchange strategy registration, and a self-built strategy like this one qualifies
  as "White Box" (transparent logic, not sold to others) — the lighter-touch category. So the
  multi-week exchange-approval process most articles describe likely does NOT apply here. Remaining:
  (1) the static IP above (also note: once registered with Dhan, IPs can't be changed for 7 days —
  confirm the VPS/provider before registering), (2) [x] **2FA/OAuth requirement resolved 2026-09-04**
  — checked directly against Dhan's own docs: our existing PIN+TOTP headless token generation IS
  Dhan's own sanctioned 2FA mechanism for programmatic access, not a workaround, and there's no
  additional per-API-call session requirement beyond it. (3) keep the existing `audit_log`/`bot.log`
  trail, which should already cover the "audit-ready logs" expectation.
- [ ] Re-review risk controls (max daily loss, per-order size caps) before any real capital is at risk.
- [ ] **Confirm with Dhan what unit the order `quantity` field actually takes for MCX, before
  live trading is ever switched on.** This is the one thing found in the 2026-09-04 strategy review
  that only you can settle, and it matters because getting it wrong scales a real order by 10x or
  100x rather than producing a slightly wrong number. Today the bot asks for `quantity=1` when a
  strategy says "buy 1 lot". That is right if Dhan counts MCX quantity in *lots*, and badly wrong if
  Dhan counts it in the underlying units (100 for a 100g Gold Mini contract, 2500 for Copper). The
  bot's own `lot_size` field can't answer the question either way -- it's a *price* multiplier (10
  for Gold Mini, because MCX quotes Gold Mini per 10g), not a contract quantity, and it is used only
  to convert P&L into rupees. The order module's docstring currently claims the caller passes
  "lot-size-scaled real contract units", which contradicts what the caller actually passes -- so the
  code and its own documentation disagree, and neither has been checked against a real MCX order.
  **Treat this as urgent rather than theoretical**: per the notes above, live trading is already
  globally armed on the VPS for ALUMINI, and a GOLDM config is sitting at `mode='live'` waiting to be
  enabled -- so the next real order placed would use whichever interpretation is currently in the
  code, unverified. **What to do:** ask Dhan support (or check a
  single real 1-lot MCX order placed manually through their web/app interface and read back what
  `quantity` the API reports for it), tell the agent the answer, and it will make the code and the
  docstring agree. Do this before flipping either live-trading switch.
- [ ] **Verify the real broker-side stop order (SL-M) mechanism against an actual placed order,
  before enabling any `risk_managed` config in live mode.** Built 2026-09-05 (`DhanOrderClient.
  place_stop_loss_market_order`/`modify_stop_loss_trigger`/`cancel_stop_loss_order`) to place a real
  resting stop at Dhan for a risk-managed position, so the exchange enforces it instantly instead of
  the bot only detecting a breach on its next ~5-minute poll. Confirmed against the installed
  `dhanhq` SDK's source code (the request shapes exist), but NOT against a real Dhan response:
  whether Dhan accepts `STOP_LOSS_MARKET` for the `MCX_COMM` segment, whether `modify_order` actually
  moves a plain SL-M order's trigger price the way it's expected to (the SDK sends a `legName` field
  on every modify call, including for a non-Super-Order, and its real effect there hasn't been
  checked), and what a filled stop order's real response looks like. **What to do**: same as the
  quantity-unit item above -- either ask Dhan support directly, or place one real small SL-M order
  manually (or via a scratch script once you're ready) on a real MCX contract and confirm it behaves
  as expected, including a manual modify and cancel. Tell the agent what you find.
- [x] **Re-run the multi-instrument backtest sweep** -- done 2026-09-04. Old 112 corrupted rows
  deleted from the real Neon database, full 144-run sweep re-run fresh. See
  `docs/backtest-results.md` for the corrected numbers -- most notably, GOLDM `rsi_mean_reversion`
  (the live config)'s CAGR corrected from a reported 60.8% to 14.6%. Still a solid strategy on its
  own merits (highest win rate in the set), but worth a fresh look now that the number that
  originally justified it has changed this much -- your call, nothing technical left to do here.



## Infrastructure setup (one-time)

- [x] Vercel project `growmore-dashboard` created (team `beautifulforce`), GitHub-connected, Neon
  Postgres provisioned and migrated.
- [x] ~~Upgrade the `beautifulforce` Vercel team to Pro, enable Vercel Authentication on
  Production~~ — **no longer planned** (decided 2026-09-04): the dashboard stays permanently on the
  `live` branch's Preview URL instead of ever being promoted to `main`/production. Preview deployments
  already get Vercel Authentication (SSO) on the Hobby plan (confirmed via your own incognito test),
  and a branch's Preview URL is stable across pushes — so this gets real access control today, without
  a Pro upgrade or a production promotion decision at all. See `docs/technical-debt.md`.
- [ ] **Confirm only your account (and anyone else you intend) is a member of the `beautifulforce`
  Vercel team** — this is what actually gates access to the Preview URL via Vercel Authentication,
  today, not something waiting on a future Pro upgrade. Worth checking now.

