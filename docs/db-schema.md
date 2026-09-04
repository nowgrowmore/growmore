# Database Schema (Neon Postgres)

One shared schema, migrations owned by `bot/` (Alembic). The dashboard reads directly; its only
writes are enable/disable toggles into `bot_config`.

```mermaid
erDiagram
    INSTRUMENTS ||--o{ BACKTEST_RUNS : "backtested on"
    INSTRUMENTS ||--o{ PAPER_POSITIONS : "held in"
    STRATEGIES ||--o{ BACKTEST_RUNS : "evaluated by"
    STRATEGIES ||--o{ PAPER_POSITIONS : "traded by"
    BACKTEST_RUNS ||--o{ BACKTEST_TRADES : "produces"
    BACKTEST_RUNS ||--o{ EQUITY_CURVE_POINTS : "produces"
    STRATEGIES ||--o{ BOT_CONFIG : "enabled/disabled per instrument"
    INSTRUMENTS ||--o{ BOT_CONFIG : "enabled/disabled per strategy"
    PAPER_POSITIONS ||--o{ PAPER_ORDERS : "filled by"
    STRATEGIES ||--o{ LIVE_POSITIONS : "traded by"
    INSTRUMENTS ||--o{ LIVE_POSITIONS : "held in"
    LIVE_POSITIONS ||--o{ LIVE_ORDERS : "filled by"
    BOT_CONFIG ||--o| BOT_SIGNAL_STATE : "current signal for"

    INSTRUMENTS {
        uuid id PK
        text symbol
        text exchange_segment "e.g. MCX_COMM"
        text security_id "Dhan instrument id"
        text name
        integer lot_size "quote-units per lot, e.g. Copper=2500 (kg, quoted per kg); GOLDM=10 (100g lot, quoted per 10g)"
        date contract_expiry "current front-month contract's last trading day, display-only"
    }
    STRATEGIES {
        uuid id PK
        text name
        text version
        jsonb params
    }
    BACKTEST_RUNS {
        uuid id PK
        uuid strategy_id FK
        uuid instrument_id FK
        timestamptz started_at
        timestamptz period_start
        timestamptz period_end
        numeric sharpe_ratio
        numeric max_drawdown_pct
        numeric win_rate_pct
        numeric profit_factor
        numeric cagr_pct
    }
    BACKTEST_TRADES {
        uuid id PK
        uuid backtest_run_id FK
        timestamptz entered_at
        timestamptz exited_at
        text side
        numeric entry_price
        numeric exit_price
        numeric pnl
    }
    EQUITY_CURVE_POINTS {
        uuid id PK
        uuid backtest_run_id FK
        timestamptz ts
        numeric equity
    }
    PAPER_POSITIONS {
        uuid id PK
        uuid strategy_id FK
        uuid instrument_id FK
        text status "open|closed"
        numeric quantity
        numeric avg_entry_price
        numeric realized_pnl
        numeric unrealized_pnl
        timestamptz opened_at
        timestamptz closed_at
    }
    PAPER_ORDERS {
        uuid id PK
        uuid paper_position_id FK
        text side "buy|sell"
        numeric quantity
        numeric simulated_fill_price
        timestamptz filled_at
        numeric pnl "realized P&L this sell fill locked in, null for buys"
    }
    LIVE_POSITIONS {
        uuid id PK
        uuid strategy_id FK
        uuid instrument_id FK
        text status "open|closed"
        numeric quantity
        numeric avg_entry_price "approximate -- see LiveTradingEngine docstring"
        numeric realized_pnl
        numeric unrealized_pnl
        timestamptz opened_at
        timestamptz closed_at
    }
    LIVE_ORDERS {
        uuid id PK
        uuid live_position_id FK
        text side "buy|sell"
        numeric quantity
        text broker_order_id "Dhan's real orderId"
        text order_status "Dhan's orderStatus (e.g. TRANSIT)"
        numeric fill_price "approximate -- the tick's quote LTP, not a confirmed fill"
        timestamptz filled_at
        numeric pnl
    }
    BOT_CONFIG {
        uuid id PK
        uuid strategy_id FK
        uuid instrument_id FK
        boolean enabled
        numeric virtual_capital
        numeric max_position_size
        numeric daily_loss_limit
        text mode "paper (default) | live -- see CLAUDE.md non-negotiables"
        boolean pending_auto_close "set when a real auto-close order failed; retried with backoff"
        integer auto_close_retry_count
        timestamptz auto_close_next_retry_at
        timestamptz updated_at
    }
    BOT_SIGNAL_STATE {
        uuid id PK
        uuid bot_config_id FK "unique -- one row per config, upserted every tick"
        text last_signal "HOLD|BUY|SELL"
        timestamptz checked_at
        numeric ltp
        numeric prev_close "previous trading day's close, for today's %% change"
        numeric daily_pnl "today's cumulative realized P&L for the daily_loss_limit guard"
        jsonb indicators "Strategy.debug_state(), display-only"
        jsonb crossing_state "Strategy.get_state_snapshot(), restored after warm-up each tick"
        timestamptz last_max_position_rejection_logged_at "throttles repeat audit_log entries to 1/30min"
    }
    BOT_STATUS {
        uuid id PK "singleton row"
        boolean live_trading_enabled
        timestamptz last_tick_at
        numeric available_balance "Dhan real fund balance"
        numeric utilized_margin
    }
    AUDIT_LOG {
        uuid id PK
        timestamptz ts
        text event_type
        jsonb payload
    }
```

## Notes

- `audit_log` records every action that could plausibly matter for a future compliance review:
  strategy enable/disable, risk-guard trips, contract rollovers, and every real order call attempt
  (`live_order_placed`/`live_order_failed`) — success or failure, always, per
  `growmore_bot/broker/dhan_order_client.py`.
- `bot_config` is the gate between "backtested" and "trading live" — nothing runs without an
  explicit `enabled` row, and a real order additionally requires `mode = "live"` on that row AND
  `Settings().live_trading_enabled = True` globally (`LIVE_TRADING_ENABLED` env var, off by default,
  still off as of 2026-09-04 — see docs/pending-actions.md before ever turning it on).
- `live_positions`/`live_orders` mirror `paper_positions`/`paper_orders` exactly (plus
  `broker_order_id`/`order_status` on `live_orders`) but are kept as fully separate tables so real
  and simulated trading data can never be confused with each other, in the database or on the
  dashboard. The dashboard reads both, filterable by a paper/live mode toggle on every page.
- `bot_signal_state` and `bot_status` are operational/display state, not user configuration — the
  former is one row per `bot_config` (what did the strategy just see), the latter a single
  process-wide singleton (is the bot armed, when did it last tick, current Dhan fund balance).
  Neither is written by the dashboard; both are upserted by the scheduler every tick.
- Money/price columns are `numeric`, never floating point.
