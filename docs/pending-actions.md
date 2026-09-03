# Pending Actions (for the account owner)

Plain-language list of things only you can do or decide. Updated as the project progresses.

## Blocking for real-price paper trading

**Timeline reality check**: because of the KYC/segment-activation turnaround below, real-price paper
trading will likely **not** be ready for the very next market open — that's outside our control.
Recommended sequence: start account opening today; the backtesting engine and bot/dashboard build
continues in parallel so everything is ready to switch on the moment API access is live.

1. [ ] **Open a Dhan Individual trading + demat account** at dhan.co if you haven't already
   (free, ₹0 AMC). Steps: mobile+email OTP signup → PAN verification → Aadhaar e-KYC → bank account
   linking (name must match PAN) → Aadhaar e-sign. Core KYC is quick (~10 min).
2. [ ] **Activate the Commodity (MCX) segment** — off by default, like F&O. In the Dhan app:
   Profile → Segment Activated → apply for Commodity → upload income proof (one of: latest salary
   slip, ITR acknowledgement, Form 16, 6-month bank statement, or a net-worth/holdings statement).
   **Takes 1–2 working days to verify** — this is the main timeline blocker, not our code.
3. [ ] **Generate a production Dhan API key** once the account (and ideally the Commodity segment)
   is active: web.dhan.co → My Profile → "Access DhanHQ APIs" (exact menu wording may have shifted
   since documented — look for the API-access section). Two options there:
   - Instant Client ID + Access Token, valid 24 hours — fine for manual/dev use, needs daily
     re-generation.
   - API key + secret (valid 12 months) → generates a fresh daily access token via a consent flow —
     better suited to an unattended bot; prefer this once we're ready to automate token refresh.
   This is separate from the Sandbox/DevPortal token you already shared (app "growmore", 30-day
   validity) — that one only simulates order placement and does **not** serve real market quotes.
   We will never use the production key to place real orders, only to read quotes/historical data.
4. [ ] **Budget for the Data API subscription**: Dhan's market-data endpoints (quotes/LTP/historical
   — what our paper-trading engine needs) are free only if the account has 25+ executed trades in
   the trailing 30 days. Since this is paper trading (no real trades), expect **₹499 + GST/month**
   until/unless real trading volume qualifies it for free.
5. [ ] Confirm you're comfortable with the production key having order-placement capability at the
   API level even though our bot will never call those endpoints (Dhan doesn't offer a data-only
   key). Access is scoped by what our code calls, not by the key itself.

## Before enabling anything beyond paper trading
- [ ] Decide per-strategy virtual capital and risk limits (defaults are placeholders in `bot/growmore_bot/config.py` — currently ₹5,00,000 per strategy, review before relying on the numbers).
- [ ] Review backtest results (Phase 3) before enabling any strategy for paper trading via the dashboard's Strategy Config page.
- [ ] Decide the initial commodity list to actually trade (default: Gold Mini, Silver Mini, Crude Oil Mini, Natural Gas — MCX).

## Before any real (live) order placement — not in scope yet
- [ ] Static IP hosting (VPS) — Dhan mandates a static IP for Order APIs.
- [ ] SEBI Algo-ID registration/tagging becomes mandatory from 2026-04-01 for API-placed orders.
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
