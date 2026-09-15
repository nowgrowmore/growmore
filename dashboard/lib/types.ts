import type { PgDate } from "./format";

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
  // Deflated Sharpe Ratio -- the probability this run's Sharpe reflects
  // real edge rather than being the luckiest of everything tried in its
  // sweep. Null until a batch job (research/validation/deflate_sweep.py
  // --persist) has computed it; it's a sweep-relative statistic, not
  // something a single run computes for itself.
  dsr: string | null;
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
  // From bot_signal_state, via bot_config -- null until the bot has ticked
  // this (strategy, instrument) pair at least once.
  current_ltp?: string | null;
  instrument_prev_close?: string | null;
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
  // Why a sell fired -- "strategy_signal", "expiry", "end_of_day", or
  // "daily_loss_limit". NULL for buy fills and for rows predating this column.
  close_reason: string | null;
  // Joined convenience fields (populated by getTradeLog()).
  strategy_id?: string;
  instrument_id?: string;
  strategy_name?: string;
  instrument_symbol?: string;
  position_status?: "open" | "closed";
  instrument_lot_size?: number | string;
  contract_expiry?: string | null;
  exchange_segment?: string;
}

export interface LivePosition {
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
  current_ltp?: string | null;
  instrument_prev_close?: string | null;
}

export interface LiveOrder {
  id: string;
  live_position_id: string;
  side: "buy" | "sell";
  quantity: string;
  broker_order_id: string;
  order_status: string;
  fill_price: string | null;
  filled_at: string;
  pnl: string | null;
  // See PaperOrder.close_reason -- same values, same meaning.
  close_reason: string | null;
  // Joined convenience fields (populated by getLiveTradeLog()).
  strategy_id?: string;
  instrument_id?: string;
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
  max_position_size: string;
  daily_loss_limit: string;
  daily_loss_limit_enabled: boolean;
  mode: "paper" | "live";
  updated_at: string;
  // Joined convenience fields.
  strategy_name?: string;
  instrument_symbol?: string;
  strategy_params?: Record<string, number | string> | null;
  // From bot_signal_state, left-joined -- null until the bot has ticked
  // this config at least once.
  last_signal?: "HOLD" | "BUY" | "SELL" | null;
  signal_checked_at?: string | null;
  signal_ltp?: string | null;
  signal_prev_close?: string | null;
  signal_indicators?: Record<string, number | string> | null;
  signal_daily_pnl?: string | null;
}

export interface BotStatus {
  id: string;
  live_trading_enabled: boolean;
  last_tick_at: string;
  available_balance: string | null;
  utilized_margin: string | null;
}

export interface PortfolioBacktestRun {
  id: string;
  universe: string; // e.g. "smallcap250"
  variant: string; // e.g. "momentum_quality_trend"
  started_at: string;
  period_start: string;
  period_end: string;
  top_n: number;
  initial_capital: string;
  final_equity: string;
  rebalance_count: number;
  sharpe_ratio: string | null;
  max_drawdown_pct: string | null;
  win_rate_pct: string | null;
  cagr_pct: string | null;
  quality_coverage_pct: string | null;
}

export interface PortfolioEquityCurvePoint {
  id: string;
  portfolio_backtest_run_id: string;
  ts: string;
  equity: string;
}

export interface PortfolioRebalanceHolding {
  id: string;
  portfolio_backtest_run_id: string;
  rebalance_date: string;
  symbol: string;
  weight: string;
  composite_score: string | null;
}

// The wheel-basket strategy (docs/stock-options-results.md): a paper-traded,
// dynamically-selected high-IV stock basket. One config manages a rotating
// set of many symbols -- unlike bot_config's one (strategy, instrument) pair
// -- so positions/legs/selections are keyed by config_id + symbol, not by
// an instruments row (NSE F&O stock options have no existing instruments
// row; that table is MCX/Dhan-security-id shaped).
export interface WheelBasketConfig {
  id: string;
  strategy_id: string;
  enabled: boolean;
  mode: string; // "paper" | "live" (live path does not exist yet)
  total_virtual_capital: string;
  top_iv_frac: string;
  rotation_hysteresis_pct: string;
  call_basis_buffer_tiers: [number, number][];
  updated_at: string;
}

export interface WheelBasketPosition {
  id: string;
  config_id: string;
  symbol: string;
  status: string; // open|closed
  state: string; // short_put|holding_shares|short_call|flat
  basis: string | null;
  shares: string;
  lots: string;
  opened_at: string;
  closed_at: string | null;
  realized_pnl: string;
  unrealized_pnl: string;
}

export interface WheelBasketLeg {
  id: string;
  position_id: string;
  // Joined in from the parent position by getWheelBasketLegs -- not a real
  // column on wheel_basket_legs itself.
  symbol: string;
  cycle_expiry: string;
  opt_type: string; // PE|CE
  strike: string;
  premium: string;
  lots: string;
  action: string; // sell_put|sell_call
  opened_at: string;
  settled_at: string | null;
  assigned: boolean;
  called_away: boolean;
  pnl: string | null;
}

export interface WheelBasketSelection {
  id: string;
  config_id: string;
  cycle_date: string;
  symbol: string;
  selected: boolean;
  avg_iv: string | null;
  iv_percentile: string | null;
  rsi: string | null;
  macd_bullish: boolean | null;
  score: string | null;
  reason: string;
  created_at: string;
}

// The MCX options-selling strategy (bot/research/mcx_options/engine.py): the
// direct MCX analog of the wheel-basket strategy above, but one config row
// per commodity (GOLDM/SILVERM), sized in lots rather than virtual capital
// -- no cross-sectional universe/rotation concept. An assigned put settles
// into a FUTURES position (not shares), which carries its own contract
// expiry independent of the option's expiry. No stop-loss ever closes a
// position by deliberate design -- only expiry (OTM), assignment (ITM put),
// or being called away (ITM call).
//
// NOTE ON DATE COLUMNS (found by independent code review, 2026-09-15): these
// were all declared `string` and are NOT strings at runtime. postgres.js
// parses `date` (oid 1082) and `timestamptz` into JS `Date` objects -- a
// `date` becoming UTC MIDNIGHT specifically. Declaring them `string` meant
// every test fixture passed a shape production never produces, and
// `title={s.cycle_date}` rendered "Mon Sep 14 2026 00:00:00 GMT+0000
// (Coordinated Universal Time)" instead of "2026-09-14". `PgDate` (see
// lib/format.ts) is the honest type; render it with `formatIstDate` /
// `formatIstDateTime`, never a bare `.toLocaleDateString()`.
export interface MCXOptionsConfig {
  id: string;
  strategy_id: string;
  enabled: boolean;
  mode: string; // "paper" | "live" (live order placement does not exist yet)
  symbol: string; // "GOLDM" | "SILVERM"
  lots: number;
  consolidating_target_delta: string;
  trend_favorable_target_delta: string;
  min_open_interest: number;
  margin_multiple_of_premium: string;
  // Flat placeholder for a futures roll's bid/ask spread, rupees per lot
  // (migration 0023) -- NOT a real sourced roll-spread figure. Surfaced on
  // the page precisely because it is a caveat worth watching.
  futures_roll_cost_per_lot: string;
  // Risk/selection flags (migration 0027). Every one is null/false by
  // default and every code path in the bot treats that as "disabled", so the
  // strategy behaves exactly as it did before 0027 until the account owner
  // sets one. Surfaced on the page precisely so an enabled one is never
  // invisible -- see docs/pending-actions.md for what each does.
  stop_loss_premium_multiple: string | null;
  min_dte_days: number | null;
  max_dte_days: number | null;
  min_credit_pct_of_strike: string | null;
  max_relative_spread: string | null;
  use_bid_for_entry_premium: boolean;
  fallback_sigma: string | null;
  updated_at: PgDate;
}

export interface MCXOptionsPosition {
  id: string;
  config_id: string;
  status: string; // open|closed
  state: string; // flat|long_futures|closed
  // Raw strike the put was assigned at, not premium-adjusted -- premium
  // received is booked as its own cash P&L at entry, never netted in.
  basis: string | null;
  futures_qty: string;
  futures_contract_expiry: PgDate | null;
  opened_at: PgDate;
  closed_at: PgDate | null;
  realized_pnl: string;
  unrealized_pnl: string;
}

export interface MCXOptionsLeg {
  id: string;
  position_id: string;
  cycle_expiry: PgDate;
  opt_type: string; // PE|CE|ROLL
  // NULL only for a "roll" leg (opt_type="ROLL") -- a futures contract
  // rollover event, which is not an option leg and has no strike/premium.
  strike: string | null;
  premium: string | null;
  lots: string;
  // sell_put | assigned | sell_call | call_expired_otm | called_away | roll
  // | put_expired_otm
  action: string;
  opened_at: PgDate;
  settled_at: PgDate | null;
  assigned: boolean;
  called_away: boolean;
  pnl: string | null;
}

// One candidate strike evaluate_candidates() looked at during strike
// selection -- see MCXOptionsSelection.candidates_considered below.
export interface MCXOptionsCandidate {
  strike: number;
  delta: number;
  oi: number;
  ltp: number;
}

// The raw JSONB shape as it actually arrives: written by the bot, never
// validated on the way back out. Anything here may be missing, null, or the
// wrong type -- see `sanitizeCandidates` in lib/mcx-options-candidates.ts,
// which is what turns this into `MCXOptionsCandidate[]`.
export type MCXOptionsCandidateRaw = Partial<Record<keyof MCXOptionsCandidate, unknown>>;

export interface MCXOptionsSelection {
  id: string;
  config_id: string;
  cycle_date: PgDate;
  // consolidating|trend_favorable|trend_unfavorable, or null for "no
  // opinion" (treated the same as trend_unfavorable: never permissive).
  regime: string | null;
  target_delta: string | null;
  selected_strike: string | null;
  reason: string;
  // Daily-snapshot fields (migration 0024) -- populated on EVERY cycle
  // (entry or hold), unlike regime/target_delta/selected_strike above which
  // stay null on cycles where entry isn't attempted at all. Null on rows
  // written before this migration.
  futures_price: string | null;
  // Snapshot of MCXOptionsPosition.state at this cycle (flat|long_futures|
  // closed), null if no position existed yet.
  position_state: string | null;
  position_basis: string | null;
  position_unrealized_pnl: string | null;
  // Every OI-surviving candidate evaluated this cycle (not just the
  // winner), null on cycles where entry isn't even attempted.
  candidates_considered: MCXOptionsCandidateRaw[] | null;
  // The expiry actually being considered this cycle (migration 0025) --
  // populated even on a SKIPPED cycle, unlike MCXOptionsLeg.cycle_expiry
  // which only exists for a cycle that wrote a leg. Null on rows written
  // before this migration.
  option_expiry: PgDate | null;
  created_at: PgDate;
  // How many candidates were REALLY evaluated this cycle, before the server
  // trimmed `candidates_considered` down to the decision-relevant subset it
  // ships (see `trimSelectionCandidates` in lib/db.ts). Not a database
  // column; null when nothing needed trimming.
  candidates_total?: number | null;
}

export interface AuditLogEntry {
  id: string;
  ts: string;
  event_type: string;
  payload: Record<string, unknown>;
}

export interface SignalHistoryRow {
  id: string;
  bot_config_id: string;
  checked_at: string;
  action: "HOLD" | "BUY" | "SELL";
  ltp: string;
}
