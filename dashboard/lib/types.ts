// Row shapes mirroring docs/db-schema.md. `numeric` columns come back from
// @neondatabase/serverless as strings (node-postgres default), so they are
// typed as `string` here and parsed on the way into formatting/aggregation
// helpers (see lib/format.ts) rather than coerced at the query layer — that
// keeps lib/db.ts a thin, honest mirror of what Postgres actually returns.

export interface Instrument {
  id: string;
  symbol: string;
  exchange_segment: string;
  security_id: string;
  name: string;
  lot_size: number | string;
  contract_expiry: string | null;
}

export interface Strategy {
  id: string;
  name: string;
  version: string;
  params: Record<string, unknown>;
}

export interface BacktestRun {
  id: string;
  strategy_id: string;
  instrument_id: string;
  started_at: string;
  period_start: string;
  period_end: string;
  sharpe_ratio: string | null;
  max_drawdown_pct: string | null;
  win_rate_pct: string | null;
  profit_factor: string | null;
  cagr_pct: string | null;
  // Joined convenience fields (populated by getBacktestRuns()).
  strategy_name?: string;
  strategy_version?: string;
  strategy_params?: Record<string, number | string> | null;
  instrument_symbol?: string;
  // count(*) from Postgres comes back as a string (bigint), same convention
  // as the numeric columns above.
  trade_count?: string | number;
}

export interface BacktestTrade {
  id: string;
  backtest_run_id: string;
  entered_at: string;
  exited_at: string | null;
  side: "buy" | "sell";
  entry_price: string;
  exit_price: string | null;
  pnl: string | null;
}

export interface EquityCurvePoint {
  id: string;
  backtest_run_id: string;
  ts: string;
  equity: string;
}

export interface PaperPosition {
  id: string;
  strategy_id: string;
  instrument_id: string;
  status: "open" | "closed";
  quantity: string;
  avg_entry_price: string;
  realized_pnl: string;
  unrealized_pnl: string;
  opened_at: string;
  closed_at: string | null;
  // Joined convenience fields.
  strategy_name?: string;
  instrument_symbol?: string;
  instrument_lot_size?: number | string;
  contract_expiry?: string | null;
}

export interface PaperOrder {
  id: string;
  paper_position_id: string;
  side: "buy" | "sell";
  quantity: string;
  simulated_fill_price: string;
  filled_at: string;
  // Realized P&L this fill locked in (lot-size-scaled); NULL for buy fills.
  pnl: string | null;
  // Joined convenience fields (populated by getTradeLog()).
  strategy_name?: string;
  instrument_symbol?: string;
  position_status?: "open" | "closed";
  instrument_lot_size?: number | string;
  contract_expiry?: string | null;
  exchange_segment?: string;
}

export interface BotConfig {
  id: string;
  strategy_id: string;
  instrument_id: string;
  enabled: boolean;
  virtual_capital: string;
  max_position_size: string;
  daily_loss_limit: string;
  updated_at: string;
  // Joined convenience fields.
  strategy_name?: string;
  instrument_symbol?: string;
}

export interface AuditLogEntry {
  id: string;
  ts: string;
  event_type: string;
  payload: Record<string, unknown>;
}
