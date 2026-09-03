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
- [x] Automatic daily token refresh built (`bot/growmore_bot/broker/token_refresh.py`,
  2026-09-03) — needs `DHAN_PIN` and `DHAN_TOTP_SECRET` in the repo-root `.env.local` to activate.
  **You don't currently have the raw TOTP secret saved** (only scanned the QR code) — most
  authenticator apps can't reveal it after the fact. Reset TOTP on Dhan
  (`web.dhan.co → Profile → DhanHQ Trading APIs → Set-up TOTP`) to get a fresh QR + text key, and
  save the text key (a password manager, not a plain note) before scanning it again. Without this,
  the bot still runs fine but you'll need to regenerate the access token by hand every 24 hours.

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
- [ ] Decide the initial commodity list to actually trade (current default: Gold Mini, Silver Mini,
  Crude Oil Mini — Natural Gas intentionally dropped).
- [ ] Decide whether to invest in a continuous/rolled futures series (splicing consecutive expired
  contract-months together) — turned out not to be needed: real per-contract history already goes
  back the full 5 years Dhan advertises (confirmed 2026-09-03), so this is no longer a blocker.

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
