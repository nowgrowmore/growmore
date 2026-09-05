# Architecture

## Overview

```mermaid
flowchart LR
    subgraph Dhan["Dhan (DhanHQ v2 API)"]
        DataAPI["Data API\n(quotes, LTP, historical OHLC)"]
        OrderAPI["Order API\n(built, gated OFF — see 'Why not live trading yet')"]
    end

    subgraph BotHost["Bot host — DigitalOcean VPS (Bangalore), since 2026-09-04"]
        Scheduler["Scheduler\n(market-hours aware, ~5min tick)"]
        Strategies["Strategies\n(SMA crossover, Donchian breakout, ...)"]
        Backtest["Backtest engine"]
        Paper["Paper trading engine\n(simulated fills, risk guards)"]
        Live["Live trading engine\n(real orders, gated OFF)"]
        Broker["Dhan client wrapper\n(Data API only)"]
        OrderClient["Dhan order client\n(the ONLY module allowed\nto call the Order API)"]
    end

    DB[("Neon Postgres\ninstruments, strategies,\nbacktest_runs, paper_orders,\npaper_positions, live_orders,\nlive_positions, bot_config,\naudit_log")]

    subgraph Vercel["Vercel"]
        Dashboard["Next.js dashboard\n(Overview, Backtests, Trade Log, Strategy Config)"]
    end

    DataAPI --> Broker --> Scheduler
    Scheduler --> Strategies --> Paper
    Strategies --> Live
    Backtest --> DB
    Paper --> DB
    Live --> DB
    Live --> OrderClient
    OrderClient -. "gated: live_trading_enabled AND\nbot_config.mode=live, both required" .-> OrderAPI
    DB --> Dashboard
    Dashboard -- "enable/disable strategy" --> DB
```

## Components

- **Dhan client wrapper** (`bot/growmore_bot/broker/dhan_client.py`): auth (daily access-token
  refresh), instrument lookup, historical OHLC, live quotes. A hard runtime allow-list (`_SafeSdk`)
  blocks it from ever calling `place_order`/`modify_order`/`cancel_order` — Data API only, always.
- **Dhan order client** (`bot/growmore_bot/broker/dhan_order_client.py`): the ONLY module allowed to
  call Dhan's real Order API. Refuses every call unless `live_trading_enabled=True` (checked at call
  time), and writes an `audit_log` entry for every attempt, success or failure. Places `MARKET`
  orders with `productType="MARGIN"` (carry-forward, not `"INTRADAY"` which would auto-square-off
  same day and break every multi-day-holding strategy this bot runs) — schema confirmed against the
  installed `dhanhq` SDK's own source, not a scraped doc page.
- **Backtest engine** (`bot/growmore_bot/backtest/`): replays historical bars, computes Sharpe/
  drawdown/win-rate/profit-factor, persists results for the dashboard's Backtests page.
- **Paper trading engine** (`bot/growmore_bot/paper/`): on each scheduler tick, fetches live quotes
  for `mode="paper"` (strategy, instrument) pairs, simulates a fill at LTP, updates
  `paper_positions`/`paper_orders`, enforces risk guards (max position size, daily loss limit,
  pre-expiry close-out).
- **Live trading engine** (`bot/growmore_bot/live/`): mirrors the paper engine's interface and risk
  guards exactly, but for `mode="live"` pairs — a BUY/SELL signal calls the Dhan order client instead
  of simulating a fill, and persists to `live_positions`/`live_orders` (never the paper tables, so
  real and simulated data can never mix). Built and tested, but real order placement stays off end
  to end today — see "Why not live trading yet."
- **Scheduler** (`bot/growmore_bot/scheduler/`): market-hours-aware polling loop, not a tick-driven
  daemon — deliberately not HFT. Enforces `contract_rollover.py`'s pre-expiry close-out guard (mirrors
  Dhan's real MCX delivery rules, automatically rolling to the next contract month once validated
  against a live quote) before evaluating any strategy for a given config, and picks the paper or
  live engine per `bot_config.mode` — a `mode="live"` config is skipped entirely, never silently
  downgraded to paper, whenever the global `live_trading_enabled` flag is off.
- **Dashboard** (`dashboard/`): Next.js on Vercel, reads the shared Postgres schema, writes only to
  `bot_config` (enable/disable toggles). Does not yet read `live_positions`/`live_orders` or expose
  `bot_config.mode` — see docs/technical-debt.md.
- **Strategy registry** (`bot/growmore_bot/strategies/registry.py`): the single name -> constructor
  map. The scheduler, the backtest sweep and the walk-forward harness all resolve
  `bot_config.strategies.name` through it, so a newly added strategy cannot work on one code path
  and fail on another.
- **Risk layer** (`bot/growmore_bot/risk/`): two composable wrappers, each of which *is* a
  `Strategy` so it drops into every registration site unchanged. `RiskManagedStrategy` adds an
  initial ATR stop, a Chandelier trail and an (unused, measured-as-a-no-op) time stop;
  `VolFilteredStrategy` refuses to OPEN while realised volatility sits in the top slice of its own
  trailing history. Exits are never suppressed by either. The authoritative stop level travels on
  `Signal.risk_state` and back via `position_state["risk"]`; the engine, not the wrapper, decides
  whether a stop was hit, testing it against the bar AFTER the one that set it.
- **Research layer** (`bot/research/`): offline, database-free analysis. `dailydata/` caches the 5y
  daily series to parquet and runs variants through the real `BacktestEngine` without touching
  Neon or needing a live Dhan token, which is what makes every result below reproducible;
  `validation/` holds the walk-forward harness (`walk_forward_run.py`) and the matched-pair
  out-of-sample comparison (`oos_pairs.py`); `currency/` decomposes MCX bullion into metal and
  rupee; `crosstrend/` and `intraday/` hold completed studies. Fold geometry and grid
  fingerprinting live in `growmore_bot/backtest/walk_forward.py` so they are unit-tested with the
  rest of the engine.
- **Database**: Neon Postgres, shared schema, bot owns migrations (Alembic).

## Deployment

- **Bot**: runs as a systemd service (`growmore-bot.service`) on a DigitalOcean droplet in Bangalore
  (moved off the owner's laptop 2026-09-04) — hardened (key-only SSH, non-root user, `ufw` +
  `fail2ban`), auto-restarts on crash/reboot. The droplet's IP still needs to be registered with Dhan
  directly before this actually satisfies their static-IP requirement for Order APIs — see
  `docs/pending-actions.md`.
- **Dashboard**: Vercel project, auto-deployed Preview on every push/PR via Vercel's GitHub
  integration; production promotion is a separate, explicitly confirmed step.
- **Database**: one Neon project for this app, isolated from other projects. Vercel Preview
  deployments get a real Neon preview branch via the Vercel↔Neon marketplace integration.

## Why not live trading yet

The real order-placement path (`dhan_order_client.py` + `live/engine.py` + `bot_config.mode`) is
built and tested (2026-09-04), but stays off end to end. Two independent gates both have to be
explicitly opened before any real order can be placed — `LIVE_TRADING_ENABLED` (global, an env var,
off by default) and a specific `bot_config.mode="live"` (per strategy/instrument pair, set directly
in the database — there's no dashboard UI for this, deliberately, so switching a config to live
trading is never an accidental checkbox click) — and even then, real infrastructure gaps remain,
tracked in `docs/technical-debt.md`/`docs/pending-actions.md`:
1. **(Resolved 2026-09-04)** Dhan requires a static IP for Order API calls, whitelisted directly with
   them. The bot runs on a VPS with a real static IP (`139.59.72.81`), now registered with Dhan —
   locked for 7 days from registration per their policy.
2. Dhan's 2FA/OAuth-based API session requirements are resolved — our existing PIN+TOTP headless
   token generation is Dhan's own sanctioned mechanism, confirmed 2026-09-04 (SEBI's Algo-ID
   *registration* itself is expected to be exempt for this bot's low order rate too).
3. Order-level fill reconciliation exists (`LiveTradingEngine.reconcile_pending_orders`, polls Dhan's
   real order status each tick) but position-level correction doesn't yet — `live_positions.
   avg_entry_price`/`realized_pnl` stay an approximation (the tick's live quote LTP) even after an
   order's own record is corrected to Dhan's real fill.
4. Tripping the daily-loss-limit guard on a live config now attempts a real closing order before
   disabling the bot_config; a failed closing order is logged as needing manual review rather than
   silently retried.

Paper trading sidesteps all of this by never calling the Order API — trades are simulated locally
against real market data.
