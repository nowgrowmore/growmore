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

    INSTRUMENTS {
        uuid id PK
        text symbol
        text exchange_segment "e.g. MCX_COMM"
        text security_id "Dhan instrument id"
        text name
        integer lot_size "real MCX contract unit, e.g. Gold Mini=100g"
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
        timestamptz updated_at
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
  dashboard. The dashboard does not read these tables yet — see docs/technical-debt.md.
- Money/price columns are `numeric`, never floating point.
