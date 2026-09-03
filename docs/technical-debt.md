# Technical Debt / Known Limitations

- **Bot runs on a local machine, no static IP.** Fine for paper trading (read-only Data API calls
  only). Blocks real order placement, which Dhan requires a static IP for. Plan: move to a small
  VPS with an elastic/static IP when live trading is actually pursued — no code change needed, just
  the new host.
- **No SEBI Algo-ID handling.** Required for any API-placed order from 2026-04-01. Not needed for
  paper trading (we never call the Order API), but must be built before any live-trading phase.
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
- **Production dashboard has no access control yet — do not promote to production until this is
  resolved.** The dashboard shows trading data and has a real write path (enable/disable strategies,
  edit risk limits via `bot_config`). Vercel Authentication (SSO) already protects Preview
  deployments, but extending it to Production requires a paid Vercel plan — attempted via API and
  confirmed blocked: `"Vercel Authentication is not available on your plan for production
  deployments"` (the `beautifulforce` team is on Hobby). Owner plans to upgrade to Vercel Pro
  "in a few days" (as of 2026-09-02) and enable Vercel Authentication on Production at that point —
  see `docs/pending-actions.md`. Until then, the `main` branch should not be promoted to production,
  since the live URL would be publicly reachable with no auth.
