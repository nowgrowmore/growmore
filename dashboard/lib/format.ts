import type { BacktestRun, PaperPosition } from "./types";

// Pure formatting/aggregation helpers. These operate on already-fetched rows
// (numeric columns arrive as strings — see lib/types.ts) so they're testable
// without a DB connection. Money/price values are parsed with Number() only
// for display; the DB itself stores them as `numeric`, never float, per
// docs/db-schema.md.

/** Parse a nullable Postgres `numeric` string into a number, defaulting to 0. */
export function toNumber(value: string | null | undefined): number {
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
  | "started_at";

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
