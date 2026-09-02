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
