# Pending Actions (for the account owner)

Plain-language list of things only you can do or decide. Updated as the project progresses.

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
  API renewal; also the real capital that would back any live order once that's ever enabled.
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
- [x] Full strategy/parameter sweep completed and **persisted to the real Neon database** 2026-09-03:
  112 backtest runs (5 strategy families × 14 parameter variants × 8 commodities), 2,586 trades,
  120,540 equity-curve points — see **[docs/backtest-results.md](backtest-results.md)** for the
  ranked top 5 and full caveats before acting on any of it (multiple-comparisons risk, no
  out-of-sample validation yet, position-sizing isn't margin-normalized across commodities). Current
  standout: **MACD Trend (5,13,5) + Gold Mini** (CAGR 78.2%, Sharpe 1.56, 91 trades) — supersedes an
  earlier, smaller 6-run pass that had wrongly favored Gold Mini + Donchian Breakout before the
  lot-size bug (see `docs/technical-debt.md`) was fixed and before RSI/MACD/Bollinger strategies
  existed to compare against.
- [x] **3 `bot_config` pairs enabled for paper trading** (2026-09-03), ₹2,50,000 virtual capital /
  1 lot max / ₹15,000 daily loss limit each: MACD (5,13,5) + Gold Mini (the top backtest pick),
  RSI Mean-Reversion (7, 30/70) + Copper, and MACD (12,26,9) + Aluminium Mini — the latter two
  chosen because real live data showed them genuinely close to a signal (RSI at 26 vs. a 30
  threshold; MACD/signal gap of ~0.2), specifically to see different strategy behaviors (a
  mean-reversion strategy vs. two different MACD parameterizations) play out for real, not just in
  backtest. All three ticking correctly as of this check.
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
- [x] **Order-level fill reconciliation + auto-close on a tripped daily loss limit, added 2026-09-04**
  — see `docs/technical-debt.md` for exactly what these do and what's still not covered (position-
  level P&L isn't retroactively corrected by reconciliation yet; a failed auto-close order leaves the
  position open for manual review rather than retrying).
- [ ] **How to actually turn this on, when the items below are ready:** (1) set `LIVE_TRADING_ENABLED
  =true` in the bot's `.env.local`, (2) directly update the specific `bot_config` row(s) you want live
  to `mode = 'live'` in the database (no dashboard UI for this, deliberately) — everything else (which
  strategy, which instrument, risk limits) stays exactly as already configured for paper trading.
  Both switches are independent; flipping only one does nothing. Ask the agent to do this when you're
  ready — don't do it via raw SQL yourself without walking through the current state of the items
  below first.
- [x] **VPS provisioned and the bot moved onto it** (2026-09-04) — DigitalOcean droplet
  `growmore-bot` (Bangalore, IP `139.59.72.81`), hardened, running as a systemd service. Verified
  ticking correctly as the sole instance (the laptop's copy was stopped to avoid double-trading
  against the shared database).
- [ ] **Register the droplet's IP (`139.59.72.81`) with Dhan** — this is the piece that actually
  satisfies the static-IP requirement; the VPS existing isn't enough on its own. This needs your own
  login to Dhan's web console (Profile → DhanHQ Trading APIs → IP whitelisting or similar — exact
  location not yet confirmed, ask the agent to check when you're ready). **Important**: once set,
  Dhan doesn't allow changing it for 7 days, so only do this once you're confident in the droplet
  setup (you are — it's been running paper trading identically to before).
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
