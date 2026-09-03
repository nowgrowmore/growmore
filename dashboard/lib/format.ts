import type { BacktestRun, PaperPosition } from "./types";

// Pure formatting/aggregation helpers. These operate on already-fetched rows
// (numeric columns arrive as strings — see lib/types.ts) so they're testable
// without a DB connection. Money/price values are parsed with Number() only
// for display; the DB itself stores them as `numeric`, never float, per
// docs/db-schema.md.

/** Parse a nullable Postgres `numeric` string into a number, defaulting to 0. */
export function toNumber(value: string | number | null | undefined): number {
  if (value === null || value === undefined || value === "") return 0;
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

export function formatCurrency(value: number, options: { signDisplay?: boolean } = {}): string {
  const formatted = new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2,
  }).format(Math.abs(value));
  if (options.signDisplay && value !== 0) {
    return `${value < 0 ? "-" : "+"}${formatted}`;
  }
  return value < 0 ? `-${formatted}` : formatted;
}

export function formatPercent(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value.toFixed(digits)}%`;
}

export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toFixed(digits);
}

/** `backtest_runs.profit_factor` is stored as NULL specifically to mean
 * "infinite" (zero losing trades in the sample) -- see
 * BacktestEngine.run_and_persist. Rendering that as "—" (the generic
 * missing-value fallback) reads as absent data rather than the very
 * different, meaningful case it actually is. */
export function formatProfitFactor(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "∞";
  return formatNumber(toNumber(value));
}

/** Compact, comparable label for a strategy's parameters, e.g.
 * "fast_period=5, slow_period=20". Sorted by key so the same strategy's
 * variants always list params in the same order. */
export function formatStrategyParams(
  params: Record<string, number | string> | null | undefined
): string {
  if (!params || Object.keys(params).length === 0) return "—";
  return Object.keys(params)
    .sort()
    .map((key) => `${key}=${params[key]}`)
    .join(", ");
}

/** "9 Sep 2025 → 1 Sep 2026" style label for a backtest's price-data window
 * (period_start/period_end) -- distinct from `started_at`, which is when the
 * backtest was actually run. */
export function formatDateRange(
  start: string | null | undefined,
  end: string | null | undefined
): string {
  if (!start || !end) return "—";
  const fmt = (iso: string) =>
    new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  return `${fmt(start)} → ${fmt(end)}`;
}

export interface PnlSummary {
  openPositionCount: number;
  totalUnrealizedPnl: number;
  totalRealizedPnl: number;
}

/**
 * Aggregate P&L summary cards from a list of paper positions. Realized P&L
 * is summed across ALL positions (open and closed); unrealized P&L only
 * applies to currently open positions but is summed defensively over
 * whatever is passed in, in case a caller already filtered.
 */
export function summarizePositions(positions: PaperPosition[]): PnlSummary {
  let openPositionCount = 0;
  let totalUnrealizedPnl = 0;
  let totalRealizedPnl = 0;

  for (const p of positions) {
    if (p.status === "open") openPositionCount += 1;
    totalUnrealizedPnl += toNumber(p.unrealized_pnl);
    totalRealizedPnl += toNumber(p.realized_pnl);
  }

  return { openPositionCount, totalUnrealizedPnl, totalRealizedPnl };
}

/** Sort key + comparator helpers for the backtests table. */
export type BacktestSortKey =
  | "sharpe_ratio"
  | "max_drawdown_pct"
  | "win_rate_pct"
  | "profit_factor"
  | "cagr_pct"
  | "started_at"
  | "trade_count";

export function sortBacktestRuns(
  runs: BacktestRun[],
  key: BacktestSortKey,
  direction: "asc" | "desc" = "desc"
): BacktestRun[] {
  const sign = direction === "asc" ? 1 : -1;
  return [...runs].sort((a, b) => {
    if (key === "started_at") {
      return sign * (new Date(a.started_at).getTime() - new Date(b.started_at).getTime());
    }
    return sign * (toNumber(a[key]) - toNumber(b[key]));
  });
}

export function filterBacktestRuns(
  runs: BacktestRun[],
  filters: { instrumentId?: string; strategyId?: string }
): BacktestRun[] {
  return runs.filter((r) => {
    if (filters.instrumentId && r.instrument_id !== filters.instrumentId) return false;
    if (filters.strategyId && r.strategy_id !== filters.strategyId) return false;
    return true;
  });
}
