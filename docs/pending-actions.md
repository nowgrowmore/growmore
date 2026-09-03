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
- [ ] **Fund the Dhan account ledger balance** — it showed ₹0.00 when the Data API subscription was
  activated, and the ₹499+GST renewal debits from that same balance in ~29 days. Add funds before
  then or the subscription may lapse.
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
- [ ] **Contract rollover reminder — no calendar alert exists yet.** As of 2026-09-03 the 8
  configured commodities' current-contract expiries are: Crude Oil Mini 2026-09-21, Nickel
  2026-09-16, Copper/Zinc Mini/Aluminium Mini/Lead Mini 2026-09-30, Gold Mini 2026-10-05, Silver
  Mini 2026-11-30. Per `docs/technical-debt.md`'s contract-rollover guard, each one will
  automatically force-close any open paper position and stop taking new trades roughly 6 (base
  metals)/8 (bullion) trading days before its own expiry — Crude Oil Mini never gets force-closed
  (cash-settled, no delivery risk). **When that happens, ask the agent to roll it**: look up the
  next front-month `security_id` from Dhan's instrument master and update that instrument's
  `security_id`/`contract_expiry` in the database — the same `bot_config` picks it up automatically,
  no other change needed. Nickel and Crude Oil Mini will need this first, likely mid-to-late
  September 2026.

## Before any real (live) order placement — not in scope yet
- [ ] Static IP hosting (VPS) — Dhan mandates a static IP for Order APIs, and this turns out to be
  part of SEBI's Algo-ID framework's requirements too, not just a Dhan-specific policy.
- [ ] **SEBI Algo-ID — smaller lift than originally thought, verified 2026-09-04.** This bot's order
  rate (polls every 5 minutes) is nowhere near the 10-orders/second-per-exchange threshold that
  triggers formal exchange strategy registration, and a self-built strategy like this one qualifies
  as "White Box" (transparent logic, not sold to others) — the lighter-touch category. So the
  multi-week exchange-approval process most articles describe likely does NOT apply here. What's
  still needed: (1) the static IP above, (2) confirm with Dhan directly what their specific
  2FA/OAuth-based API session requirements are for retail algo API access — our current setup uses a
  long-lived access token refreshed via TOTP, which may not satisfy a per-session OAuth+2FA
  expectation, worth a direct check before going live, (3) keep the existing `audit_log`/`bot.log`
  trail, which should already cover the "audit-ready logs" expectation.
- [ ] Re-review risk controls (max daily loss, per-order size caps) before any real capital is at risk.

## Infrastructure setup (one-time)
- [x] Vercel project `growmore-dashboard` created (team `beautifulforce`), GitHub-connected, Neon
  Postgres provisioned and migrated.
- [ ] **Upgrade the `beautifulforce` Vercel team to Pro**, then enable Vercel Authentication (SSO)
  on Production deployments (Project Settings → Deployment Protection → set to cover Production).
  You said you'll do this "in a few days" (as of 2026-09-02). **Do not merge to `main`/promote to
  production before this is done** — right now the dashboard has a real write path (enable/disable
  strategies, edit risk limits) and no access control on production, only on Preview. See
  `docs/technical-debt.md`.
- [ ] Once on Pro and Vercel Authentication is enabled for Production, confirm only your account
  (and anyone else you intend) is a member of the `beautifulforce` Vercel team, since team
  membership is what gates access.
