# growmore

Personal MCX-commodity trading system: backtest strategies, then paper-trade the winners with real
market data (via Dhan's API), with a dashboard for analytics. **Paper trading only** — no real
orders are ever placed. See `docs/architecture.md` for the full design and `docs/pending-actions.md`
for what's needed from you before each phase.

## Layout

- `bot/` — Python trading engine (backtesting, paper trading, Dhan API client). See `bot/README.md`.
- `dashboard/` — Next.js dashboard on Vercel/Neon. See `dashboard/README.md`.
- `docs/` — architecture, DB schema, technical debt, pending actions.

## Status

Early build-out — see `docs/pending-actions.md` for what's blocking real-price paper trading.
