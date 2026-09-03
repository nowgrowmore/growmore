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
    }
    BOT_CONFIG {
        uuid id PK
        uuid strategy_id FK
        uuid instrument_id FK
        boolean enabled
        numeric virtual_capital
        numeric max_position_size
        numeric daily_loss_limit
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
  strategy enable/disable, risk-guard trips, and (once live trading exists) every real order call.
- `bot_config` is the single gate between "backtested" and "paper trading live" — nothing runs
  without an explicit enabled row.
- Money/price columns are `numeric`, never floating point.
