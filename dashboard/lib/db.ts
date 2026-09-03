import postgres from "postgres";
import type {
  BacktestRun,
  BacktestTrade,
  BotConfig,
  EquityCurvePoint,
  PaperOrder,
  PaperPosition,
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
  cachedClient = wrap(postgres(url));
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
      i.contract_expiry as contract_expiry
    from paper_positions p
    join strategies s on s.id = p.strategy_id
    join instruments i on i.id = p.instrument_id
    order by coalesce(p.closed_at, p.opened_at) desc
    limit ${limit}
  `;
  return rows as unknown as PaperPosition[];
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
      i.symbol as instrument_symbol
    from bot_config c
    join strategies s on s.id = c.strategy_id
    join instruments i on i.id = c.instrument_id
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
  virtualCapital: number;
}

export async function updateBotConfigRiskParams(id: string, params: RiskParams): Promise<void> {
  const sql = getClient();
  const { maxPositionSize, dailyLossLimit, virtualCapital } = params;
  await sql.transaction((tx) => [
    tx`
      update bot_config
      set
        max_position_size = ${maxPositionSize},
        daily_loss_limit = ${dailyLossLimit},
        virtual_capital = ${virtualCapital},
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
