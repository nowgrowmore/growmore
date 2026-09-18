import postgres from "postgres";
import { rankCandidates, sanitizeCandidates } from "./mcx-options-candidates";
import type {
  AuditLogEntry,
  BacktestRun,
  BacktestTrade,
  BotConfig,
  BotStatus,
  EquityCurvePoint,
  LiveOrder,
  LivePosition,
  PaperOrder,
  PaperPosition,
  PortfolioBacktestRun,
  PortfolioEquityCurvePoint,
  PortfolioRebalanceHolding,
  SignalHistoryRow,
  WheelBasketConfig,
  WheelBasketLeg,
  WheelBasketPosition,
  WheelBasketSelection,
  MCXOptionsConfig,
  MCXOptionsLeg,
  MCXOptionsPosition,
  MCXOptionsSelection,
} from "./types";

// Thin, typed query layer against the shared Postgres schema owned by
// `bot/` (Alembic). This app never creates or alters tables — see
// docs/db-schema.md for the source of truth.
//
// The client is created lazily (not at module load) so that importing this
// file in a test file never requires DATABASE_URL to be set, and so tests
// can swap in a fake client via __setTestClient.
//
// Uses the `postgres` package (plain TCP wire protocol) rather than
// @neondatabase/serverless's HTTP-only client: the latter can only reach
// Neon's own HTTP proxy and cannot connect to a local/dockerized Postgres at
// all, which broke local dev and any DB-backed testing against the
// local/CI Postgres this project's conventions call for. `postgres` works
// unchanged against both a local Postgres and a real Neon database (Neon
// exposes a standard wire-protocol endpoint too, not just the HTTP one).

type SqlTag = (strings: TemplateStringsArray, ...params: unknown[]) => Promise<unknown[]>;
export type SqlClient = SqlTag & {
  transaction: (
    queriesOrFn: Promise<unknown>[] | ((tx: SqlClient) => Promise<unknown>[])
  ) => Promise<unknown[]>;
};

function wrap(raw: postgres.Sql): SqlClient {
  const client = raw as unknown as SqlClient;
  client.transaction = (queriesOrFn) =>
    raw.begin((tx) => {
      const txClient = wrap(tx as unknown as postgres.Sql);
      const queries = typeof queriesOrFn === "function" ? queriesOrFn(txClient) : queriesOrFn;
      return Promise.all(queries);
    });
  return client;
}

let cachedClient: SqlClient | null = null;

function getClient(): SqlClient {
  if (cachedClient) return cachedClient;
  const url = process.env.DATABASE_URL;
  if (!url) {
    throw new Error(
      "DATABASE_URL is not set. Copy .env.example to .env.local (or .env.test.local for tests) and fill it in."
    );
  }
  // `prepare: false` -- DATABASE_URL is Neon's POOLED connection string
  // (PgBouncer-style transaction pooling), which is fundamentally
  // incompatible with postgres.js's default server-side prepared
  // statements: a warm serverless instance's cached connection can hold a
  // prepared statement's plan from before a schema change (e.g. an Alembic
  // migration adding/altering a column), and PgBouncer's transaction-mode
  // pooling means that connection can get handed to a query against the
  // NEW schema, which Postgres then rejects with "cached plan must not
  // change result type" -- confirmed live 2026-09-14 immediately after
  // migrations 0023/0024 altered mcx_options_legs/mcx_options_selections,
  // crashing every /mcx-options request until this fix (a fresh deploy
  // alone wasn't enough to reliably clear it, since the same class of
  // failure could recur on the next schema change). Disabling prepared
  // statements is Neon's own documented fix for postgres.js against the
  // pooled endpoint -- see https://neon.tech/docs/guides/postgres-js.
  cachedClient = wrap(postgres(url, { prepare: false }));
  return cachedClient;
}

/**
 * Test-only seam: inject a fake `sql` tagged-template function so unit tests
 * never open a real DB connection. Call with `null` to reset.
 */
export function __setTestClient(client: SqlClient | null): void {
  cachedClient = client;
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

export async function getOpenPaperPositions(): Promise<PaperPosition[]> {
  const sql = getClient();
  const rows = await sql`
    select
      p.*,
      s.name as strategy_name,
      i.symbol as instrument_symbol,
      i.lot_size as instrument_lot_size,
      i.contract_expiry as contract_expiry
    from paper_positions p
    join strategies s on s.id = p.strategy_id
    join instruments i on i.id = p.instrument_id
    where p.status = 'open'
    order by p.opened_at desc
  `;
  return rows as unknown as PaperPosition[];
}

export async function getAllPaperPositions(limit = 200): Promise<PaperPosition[]> {
  const sql = getClient();
  const rows = await sql`
    select
      p.*,
      s.name as strategy_name,
      i.symbol as instrument_symbol,
      i.lot_size as instrument_lot_size,
      i.contract_expiry as contract_expiry,
      st.ltp as current_ltp,
      st.prev_close as instrument_prev_close
    from paper_positions p
    join strategies s on s.id = p.strategy_id
    join instruments i on i.id = p.instrument_id
    left join bot_config c
      on c.strategy_id = p.strategy_id and c.instrument_id = p.instrument_id and c.mode = 'paper'
    left join bot_signal_state st on st.bot_config_id = c.id
    order by coalesce(p.closed_at, p.opened_at) desc
    limit ${limit}
  `;
  return rows as unknown as PaperPosition[];
}

export async function getAllLivePositions(limit = 200): Promise<LivePosition[]> {
  const sql = getClient();
  const rows = await sql`
    select
      p.*,
      s.name as strategy_name,
      i.symbol as instrument_symbol,
      i.lot_size as instrument_lot_size,
      i.contract_expiry as contract_expiry,
      st.ltp as current_ltp,
      st.prev_close as instrument_prev_close
    from live_positions p
    join strategies s on s.id = p.strategy_id
    join instruments i on i.id = p.instrument_id
    left join bot_config c
      on c.strategy_id = p.strategy_id and c.instrument_id = p.instrument_id and c.mode = 'live'
    left join bot_signal_state st on st.bot_config_id = c.id
    order by coalesce(p.closed_at, p.opened_at) desc
    limit ${limit}
  `;
  return rows as unknown as LivePosition[];
}

export async function getLiveTradeLog(limit = 100): Promise<LiveOrder[]> {
  const sql = getClient();
  const rows = await sql`
    select
      o.*,
      p.strategy_id as strategy_id,
      p.instrument_id as instrument_id,
      s.name as strategy_name,
      i.symbol as instrument_symbol,
      i.lot_size as instrument_lot_size,
      i.contract_expiry as contract_expiry,
      i.exchange_segment as exchange_segment,
      p.status as position_status
    from live_orders o
    join live_positions p on p.id = o.live_position_id
    join strategies s on s.id = p.strategy_id
    join instruments i on i.id = p.instrument_id
    order by o.filled_at desc
    limit ${limit}
  `;
  return rows as unknown as LiveOrder[];
}

export interface BacktestRunFilters {
  instrumentId?: string;
  strategyId?: string;
}

export async function getBacktestRuns(filters: BacktestRunFilters = {}): Promise<BacktestRun[]> {
  const sql = getClient();
  const { instrumentId, strategyId } = filters;
  const rows = await sql`
    select
      b.*,
      s.name as strategy_name,
      s.version as strategy_version,
      s.params as strategy_params,
      i.symbol as instrument_symbol,
      (
        select count(*) from backtest_trades t
        where t.backtest_run_id = b.id and t.pnl is not null
      ) as trade_count
    from backtest_runs b
    join strategies s on s.id = b.strategy_id
    join instruments i on i.id = b.instrument_id
    where (${instrumentId ?? null}::uuid is null or b.instrument_id = ${instrumentId ?? null})
      and (${strategyId ?? null}::uuid is null or b.strategy_id = ${strategyId ?? null})
    order by b.started_at desc
  `;
  return rows as unknown as BacktestRun[];
}

export async function getEquityCurve(backtestRunId: string): Promise<EquityCurvePoint[]> {
  const sql = getClient();
  const rows = await sql`
    select *
    from equity_curve_points
    where backtest_run_id = ${backtestRunId}
    order by ts asc
  `;
  return rows as unknown as EquityCurvePoint[];
}

export async function getBacktestTrades(backtestRunId: string): Promise<BacktestTrade[]> {
  const sql = getClient();
  const rows = await sql`
    select *
    from backtest_trades
    where backtest_run_id = ${backtestRunId}
    order by entered_at asc
  `;
  return rows as unknown as BacktestTrade[];
}

export async function getTradeLog(limit = 100): Promise<PaperOrder[]> {
  const sql = getClient();
  const rows = await sql`
    select
      o.*,
      p.strategy_id as strategy_id,
      p.instrument_id as instrument_id,
      s.name as strategy_name,
      i.symbol as instrument_symbol,
      i.lot_size as instrument_lot_size,
      i.contract_expiry as contract_expiry,
      i.exchange_segment as exchange_segment,
      p.status as position_status
    from paper_orders o
    join paper_positions p on p.id = o.paper_position_id
    join strategies s on s.id = p.strategy_id
    join instruments i on i.id = p.instrument_id
    order by o.filled_at desc
    limit ${limit}
  `;
  return rows as unknown as PaperOrder[];
}

export async function getBotConfigs(): Promise<BotConfig[]> {
  const sql = getClient();
  const rows = await sql`
    select
      c.*,
      s.name as strategy_name,
      s.params as strategy_params,
      i.symbol as instrument_symbol,
      st.last_signal as last_signal,
      st.checked_at as signal_checked_at,
      st.ltp as signal_ltp,
      st.prev_close as signal_prev_close,
      st.indicators as signal_indicators,
      st.daily_pnl as signal_daily_pnl
    from bot_config c
    join strategies s on s.id = c.strategy_id
    join instruments i on i.id = c.instrument_id
    left join bot_signal_state st on st.bot_config_id = c.id
    order by s.name asc, i.symbol asc
  `;
  return rows as unknown as BotConfig[];
}

// ---------------------------------------------------------------------------
// Writes — the only write path this app has. Every write also appends to
// audit_log per docs/db-schema.md ("audit_log records every action that
// could plausibly matter for a future compliance review: strategy
// enable/disable, risk-guard trips, ...").
// ---------------------------------------------------------------------------

export async function setBotConfigEnabled(id: string, enabled: boolean): Promise<void> {
  const sql = getClient();
  await sql.transaction((tx) => [
    tx`
      update bot_config
      set enabled = ${enabled}, updated_at = now()
      where id = ${id}
    `,
    tx`
      insert into audit_log (id, ts, event_type, payload)
      values (
        gen_random_uuid(),
        now(),
        ${enabled ? "strategy_enabled" : "strategy_disabled"},
        ${JSON.stringify({ bot_config_id: id, enabled })}::jsonb
      )
    `,
  ]);
}

export interface RiskParams {
  maxPositionSize: number;
  dailyLossLimit: number;
  dailyLossLimitEnabled: boolean;
}

export async function updateBotConfigRiskParams(id: string, params: RiskParams): Promise<void> {
  const sql = getClient();
  const { maxPositionSize, dailyLossLimit, dailyLossLimitEnabled } = params;
  await sql.transaction((tx) => [
    tx`
      update bot_config
      set
        max_position_size = ${maxPositionSize},
        daily_loss_limit = ${dailyLossLimit},
        daily_loss_limit_enabled = ${dailyLossLimitEnabled},
        updated_at = now()
      where id = ${id}
    `,
    tx`
      insert into audit_log (id, ts, event_type, payload)
      values (
        gen_random_uuid(),
        now(),
        'risk_params_updated',
        ${JSON.stringify({ bot_config_id: id, ...params })}::jsonb
      )
    `,
  ]);
}

export async function getBotStatus(): Promise<BotStatus | null> {
  const sql = getClient();
  const rows = await sql`select * from bot_status limit 1`;
  return (rows[0] as unknown as BotStatus) ?? null;
}

export async function getAuditLog(limit = 200): Promise<AuditLogEntry[]> {
  const sql = getClient();
  const rows = await sql`
    select * from audit_log
    order by ts desc
    limit ${limit}
  `;
  return rows as unknown as AuditLogEntry[];
}

/** The last `limit` ticks' signals for one bot_config, oldest first -- for
 * the dashboard's "recent signal history" strip. Unlike bot_signal_state
 * (upsert-only, one row per config), signal_history is append-only: one row
 * per tick regardless of whether the signal was HOLD/BUY/SELL. */
export async function getRecentSignals(
  botConfigId: string,
  limit = 5
): Promise<SignalHistoryRow[]> {
  const sql = getClient();
  const rows = await sql`
    select * from signal_history
    where bot_config_id = ${botConfigId}
    order by checked_at desc
    limit ${limit}
  `;
  return (rows as unknown as SignalHistoryRow[]).reverse();
}

/** Same as getRecentSignals, batched for every config on a page in one round
 * trip -- a window function keeps the last `limit` rows per bot_config_id
 * server-side rather than running one query per card. Returns a map keyed
 * by bot_config_id, each value oldest-first (empty array if a config has no
 * history yet). */
export async function getRecentSignalsForConfigs(
  botConfigIds: string[],
  limit = 5
): Promise<Record<string, SignalHistoryRow[]>> {
  const byConfig: Record<string, SignalHistoryRow[]> = {};
  for (const id of botConfigIds) byConfig[id] = [];
  if (botConfigIds.length === 0) return byConfig;

  const sql = getClient();
  const rows = await sql`
    select * from (
      select *, row_number() over (partition by bot_config_id order by checked_at desc) as rn
      from signal_history
      where bot_config_id = any(${botConfigIds}::uuid[])
    ) ranked
    where rn <= ${limit}
    order by bot_config_id, checked_at asc
  `;
  for (const row of rows as unknown as (SignalHistoryRow & { rn: number })[]) {
    byConfig[row.bot_config_id]?.push(row);
  }
  return byConfig;
}

/** The most recent audit_log entry that changed one bot_config's enabled
 * state -- a manual toggle (payload has bot_config_id directly) or a
 * daily-loss-limit trip (payload has bot_config_id since the 2026-09-05 fix
 * -- older trip rows predating that fix won't match, and this returns null
 * for them, same as showing nothing extra today). */
export async function getLastConfigStateChange(botConfigId: string): Promise<AuditLogEntry | null> {
  const sql = getClient();
  const rows = await sql`
    select * from audit_log
    where event_type in (
      'strategy_enabled', 'strategy_disabled',
      'risk_guard_daily_loss_limit_tripped', 'live_risk_guard_daily_loss_limit_tripped'
    )
    and payload->>'bot_config_id' = ${botConfigId}
    order by ts desc
    limit 1
  `;
  return (rows[0] as unknown as AuditLogEntry) ?? null;
}

/** Batched version of getLastConfigStateChange for a page rendering many
 * configs at once (e.g. 12 disabled live configs) -- one query instead of
 * one per config. Returns a map keyed by bot_config_id; a config with no
 * matching row (predates the payload fix, or was never toggled) is simply
 * absent from the map. */
export async function getLastConfigStateChangeForConfigs(
  botConfigIds: string[]
): Promise<Record<string, AuditLogEntry>> {
  const byConfig: Record<string, AuditLogEntry> = {};
  if (botConfigIds.length === 0) return byConfig;

  const sql = getClient();
  const rows = await sql`
    select distinct on (payload->>'bot_config_id') *
    from audit_log
    where event_type in (
      'strategy_enabled', 'strategy_disabled',
      'risk_guard_daily_loss_limit_tripped', 'live_risk_guard_daily_loss_limit_tripped'
    )
    and payload->>'bot_config_id' = any(${botConfigIds})
    order by payload->>'bot_config_id', ts desc
  `;
  for (const row of rows as unknown as AuditLogEntry[]) {
    byConfig[row.payload.bot_config_id as string] = row;
  }
  return byConfig;
}

// ---------------------------------------------------------------------------
// Small-cap momentum research (bot/research/smallcap_momentum/) -- real-data
// backtests on an asset class this bot doesn't trade, never wired into the
// live scheduler. See docs/smallcap-momentum-backtest-results.md.
// ---------------------------------------------------------------------------

export async function getPortfolioBacktestRuns(): Promise<PortfolioBacktestRun[]> {
  const sql = getClient();
  const rows = await sql`
    select * from portfolio_backtest_runs
    order by universe asc, cagr_pct desc
  `;
  return rows as unknown as PortfolioBacktestRun[];
}

export async function getPortfolioEquityCurve(
  runId: string
): Promise<PortfolioEquityCurvePoint[]> {
  const sql = getClient();
  const rows = await sql`
    select * from portfolio_equity_curve_points
    where portfolio_backtest_run_id = ${runId}
    order by ts asc
  `;
  return rows as unknown as PortfolioEquityCurvePoint[];
}

export async function getPortfolioHoldings(runId: string): Promise<PortfolioRebalanceHolding[]> {
  const sql = getClient();
  const rows = await sql`
    select * from portfolio_rebalance_holdings
    where portfolio_backtest_run_id = ${runId}
    order by rebalance_date asc, weight desc
  `;
  return rows as unknown as PortfolioRebalanceHolding[];
}

// The wheel-basket strategy (docs/stock-options-results.md) -- a paper-
// traded, dynamically-selected high-IV stock basket. One config manages
// many rotating symbols, unlike bot_config's one-instrument-per-row shape.

export async function getWheelBasketConfigs(): Promise<WheelBasketConfig[]> {
  const sql = getClient();
  const rows = await sql`
    select * from wheel_basket_configs
    order by updated_at desc
  `;
  return rows as unknown as WheelBasketConfig[];
}

export async function getWheelBasketPositions(configId: string): Promise<WheelBasketPosition[]> {
  const sql = getClient();
  const rows = await sql`
    select * from wheel_basket_positions
    where config_id = ${configId}
    order by opened_at desc
  `;
  return rows as unknown as WheelBasketPosition[];
}

export async function getWheelBasketLegs(configId: string): Promise<WheelBasketLeg[]> {
  const sql = getClient();
  // p.symbol is joined in purely for display -- WheelBasketLeg itself has
  // no symbol column (that lives on the parent position), mirrored here so
  // the trade-history table doesn't need a second round trip per leg.
  const rows = await sql`
    select l.*, p.symbol as symbol from wheel_basket_legs l
    join wheel_basket_positions p on p.id = l.position_id
    where p.config_id = ${configId}
    order by l.opened_at desc
  `;
  return rows as unknown as WheelBasketLeg[];
}

export async function getWheelBasketSelections(
  configId: string,
  limit = 300
): Promise<WheelBasketSelection[]> {
  const sql = getClient();
  const rows = await sql`
    select * from wheel_basket_selections
    where config_id = ${configId}
    order by cycle_date desc, score desc nulls last
    limit ${limit}
  `;
  return rows as unknown as WheelBasketSelection[];
}

export async function setWheelBasketConfigEnabled(id: string, enabled: boolean): Promise<void> {
  const sql = getClient();
  await sql.transaction((tx) => [
    tx`
      update wheel_basket_configs
      set enabled = ${enabled}, updated_at = now()
      where id = ${id}
    `,
    tx`
      insert into audit_log (id, ts, event_type, payload)
      values (
        gen_random_uuid(),
        now(),
        ${enabled ? "wheel_basket_enabled" : "wheel_basket_disabled"},
        ${JSON.stringify({ wheel_basket_config_id: id, enabled })}::jsonb
      )
    `,
  ]);
}

// The MCX options-selling strategy (bot/research/mcx_options/engine.py) --
// the direct MCX analog of the wheel-basket strategy above: one config row
// per commodity (GOLDM/SILVERM), sized in lots rather than virtual capital.

export async function getMCXOptionsConfigs(): Promise<MCXOptionsConfig[]> {
  const sql = getClient();
  const rows = await sql`
    select * from mcx_options_configs
    order by updated_at desc
  `;
  return rows as unknown as MCXOptionsConfig[];
}

// The /mcx-options page used to issue 3N + 1 queries -- one per config for
// each of positions/legs/selections -- under `force-dynamic` with a 60s
// AutoRefresh, so every open tab replayed all of them every minute. These
// batched variants take every config id at once, the same idiom
// `getRecentSignalsForConfigs`/`getLastConfigStateChangeForConfigs` above
// already use. Found by independent code review, 2026-09-15.

export async function getMCXOptionsPositionsForConfigs(
  configIds: string[]
): Promise<Record<string, MCXOptionsPosition[]>> {
  const byConfig: Record<string, MCXOptionsPosition[]> = {};
  for (const id of configIds) byConfig[id] = [];
  if (configIds.length === 0) return byConfig;

  const sql = getClient();
  const rows = await sql`
    select * from mcx_options_positions
    where config_id = any(${configIds}::uuid[])
    order by opened_at desc
  `;
  for (const row of rows as unknown as MCXOptionsPosition[]) {
    byConfig[row.config_id]?.push(row);
  }
  return byConfig;
}

export async function getMCXOptionsLegsForConfigs(
  configIds: string[]
): Promise<Record<string, MCXOptionsLeg[]>> {
  const byConfig: Record<string, MCXOptionsLeg[]> = {};
  for (const id of configIds) byConfig[id] = [];
  if (configIds.length === 0) return byConfig;

  const sql = getClient();
  const rows = await sql`
    select l.*, p.config_id from mcx_options_legs l
    join mcx_options_positions p on p.id = l.position_id
    where p.config_id = any(${configIds}::uuid[])
    order by l.opened_at desc
  `;
  for (const row of rows as unknown as (MCXOptionsLeg & { config_id: string })[]) {
    byConfig[row.config_id]?.push(row);
  }
  return byConfig;
}

export async function getMCXOptionsSelectionsForConfigs(
  configIds: string[],
  limit = 300
): Promise<Record<string, MCXOptionsSelection[]>> {
  const byConfig: Record<string, MCXOptionsSelection[]> = {};
  for (const id of configIds) byConfig[id] = [];
  if (configIds.length === 0) return byConfig;

  const sql = getClient();
  const rows = await sql`
    select * from (
      select *, row_number() over (
        partition by config_id order by cycle_date desc, created_at desc
      ) as rn
      from mcx_options_selections
      where config_id = any(${configIds}::uuid[])
    ) ranked
    where rn <= ${limit}
    order by cycle_date desc, created_at desc
  `;
  for (const row of rows as unknown as (MCXOptionsSelection & { rn: number })[]) {
    byConfig[row.config_id]?.push(trimSelectionCandidates(row));
  }
  return byConfig;
}

//: A single cycle's `candidates_considered` can hold every strike on the
//: chain -- the first real production cycles evaluated 144 (GOLDM) and 176
//: (SILVERM). Shipping 300 such rows over the RSC wire to render ten entries
//: each is most of this page's payload. Trim to the decision-relevant set
//: here, on the server, using the SAME ranking helper the client renders with
//: so the two can never disagree, and carry the original count through as
//: `candidates_total` so the UI's "+N more" stays truthful.
const MAX_CANDIDATES_SENT = 25;

function trimSelectionCandidates(row: MCXOptionsSelection): MCXOptionsSelection {
  const clean = sanitizeCandidates(row.candidates_considered);
  if (clean === null || clean.length <= MAX_CANDIDATES_SENT) {
    return { ...row, candidates_total: clean?.length ?? null };
  }
  return {
    ...row,
    candidates_considered: rankCandidates(clean, {
      selectedStrike: row.selected_strike !== null ? Number(row.selected_strike) : null,
      targetDelta: row.target_delta !== null ? Number(row.target_delta) : null,
      limit: MAX_CANDIDATES_SENT,
    }),
    candidates_total: clean.length,
  };
}

//: Contract multiplier per MCX symbol. The /mcx-options exposure card needs
//: it because strikes and premiums are quoted PER UNIT while the money at
//: risk is `strike x lots x lot_size` -- a factor of 10 for GOLDM and 5 for
//: SILVERM. Keyed by symbol rather than instrument id because
//: `mcx_options_configs` references its commodity by symbol, not by FK.
export async function getLotSizesBySymbol(): Promise<Record<string, number>> {
  const sql = getClient();
  const rows = await sql`
    select symbol, lot_size from instruments
  `;
  return Object.fromEntries(
    (rows as unknown as { symbol: string; lot_size: number }[]).map((r) => [
      r.symbol,
      Number(r.lot_size),
    ])
  );
}

export async function setMCXOptionsConfigEnabled(id: string, enabled: boolean): Promise<void> {
  const sql = getClient();
  await sql.transaction((tx) => [
    tx`
      update mcx_options_configs
      set enabled = ${enabled}, updated_at = now()
      where id = ${id}
    `,
    tx`
      insert into audit_log (id, ts, event_type, payload)
      values (
        gen_random_uuid(),
        now(),
        ${enabled ? "mcx_options_enabled" : "mcx_options_disabled"},
        ${JSON.stringify({ mcx_options_config_id: id, enabled })}::jsonb
      )
    `,
  ]);
}
