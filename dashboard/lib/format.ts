import type { BacktestRun, PaperPosition } from "./types";
import { getStrategyInfo } from "./strategy-info";

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

/** Today's % change: (current - prevClose) / prevClose * 100, signed. `null`
 * when there's no prevClose yet (bot hasn't ticked this instrument today) or
 * prevClose is 0 (would divide by zero) -- render as "—", not a false 0%. */
export function computePctChangeToday(
  current: string | number | null | undefined,
  prevClose: string | number | null | undefined
): number | null {
  const currentNum = toNumber(current);
  const prevCloseNum = toNumber(prevClose);
  if (!prevCloseNum || current === null || current === undefined || current === "") return null;
  return ((currentNum - prevCloseNum) / prevCloseNum) * 100;
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

/** Renders a single param value -- recurses into nested objects (e.g.
 * regime_switch's `macd_params`/`ranging_params`) as `{k=v, k=v}` instead of
 * the default `String(value)` for an object, which is the useless
 * "[object Object]" that used to hide exactly the values that distinguish
 * one regime_switch variant from another. */
function formatParamValue(value: unknown): string {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    const inner = Object.keys(value as Record<string, unknown>)
      .sort()
      .map((k) => `${k}=${formatParamValue((value as Record<string, unknown>)[k])}`)
      .join(", ");
    return `{${inner}}`;
  }
  return String(value);
}

/** Compact, comparable label for a strategy's parameters, e.g.
 * "fast_period=5, slow_period=20". Sorted by key so the same strategy's
 * variants always list params in the same order. */
export function formatStrategyParams(
  params: Record<string, unknown> | null | undefined
): string {
  if (!params || Object.keys(params).length === 0) return "—";
  return Object.keys(params)
    .sort()
    .map((key) => `${key}=${formatParamValue(params[key])}`)
    .join(", ");
}

/** Multi-line, human-readable explanation of a strategy's parameters for a
 * hover tooltip, e.g. "fast_period=5 -- Bars averaged for the quick-reacting
 * moving average...". Falls back to the plain key=value label (no
 * explanation available) for a strategy name not in STRATEGY_INFO. */
export function formatStrategyParamsTooltip(
  strategyName: string,
  params: Record<string, unknown> | null | undefined
): string {
  if (!params || Object.keys(params).length === 0) return "";
  const info = getStrategyInfo(strategyName);
  return Object.keys(params)
    .sort()
    .map((key) => {
      const paramInfo = info?.params[key];
      const value = formatParamValue(params[key]);
      return paramInfo ? `${key}=${value} -- ${paramInfo.explain}` : `${key}=${value}`;
    })
    .join("\n");
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
  /** realized + unrealized across everything passed in. */
  netPnl: number;
  closedTradeCount: number;
  /** % of closed positions with realized_pnl > 0. Null with no closed trades yet -- distinct from 0%. */
  winRatePct: number | null;
  /** Highest/lowest realized_pnl among closed positions. Null with no closed trades yet. */
  bestTrade: number | null;
  worstTrade: number | null;
}

/**
 * Aggregate P&L summary cards from a list of paper positions. Realized P&L
 * is summed across ALL positions (open and closed); unrealized P&L only
 * applies to currently open positions but is summed defensively over
 * whatever is passed in, in case a caller already filtered. Win
 * rate/best/worst trade only consider CLOSED positions -- an open position's
 * unrealized P&L hasn't been "won" or "lost" yet.
 */
export function summarizePositions(positions: PaperPosition[]): PnlSummary {
  let openPositionCount = 0;
  let totalUnrealizedPnl = 0;
  let totalRealizedPnl = 0;
  let closedTradeCount = 0;
  let winCount = 0;
  let bestTrade: number | null = null;
  let worstTrade: number | null = null;

  for (const p of positions) {
    if (p.status === "open") openPositionCount += 1;
    totalUnrealizedPnl += toNumber(p.unrealized_pnl);
    totalRealizedPnl += toNumber(p.realized_pnl);

    if (p.status === "closed") {
      closedTradeCount += 1;
      const pnl = toNumber(p.realized_pnl);
      if (pnl > 0) winCount += 1;
      bestTrade = bestTrade === null ? pnl : Math.max(bestTrade, pnl);
      worstTrade = worstTrade === null ? pnl : Math.min(worstTrade, pnl);
    }
  }

  return {
    openPositionCount,
    totalUnrealizedPnl,
    totalRealizedPnl,
    netPnl: totalUnrealizedPnl + totalRealizedPnl,
    closedTradeCount,
    winRatePct: closedTradeCount === 0 ? null : (winCount / closedTradeCount) * 100,
    bestTrade,
    worstTrade,
  };
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

/** "3 min ago" / "2h ago" style relative label, falling back to a full
 * timestamp past 24h. `null`/`undefined` reads as "never" (bot hasn't
 * ticked yet). */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  const minutes = Math.round(ms / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return new Date(iso).toLocaleString();
}

export interface DailyLossProgress {
  /** 0-100+, how much of the daily loss limit has been used (loss only --
   * a positive daily_pnl reads as 0% used, not negative). */
  usedPct: number;
  /** Today's realized P&L, as a plain number. */
  dailyPnl: number;
  limit: number;
  severity: "ok" | "warning" | "critical";
}

/** How much of a config's daily_loss_limit has been eaten into by today's
 * realized P&L. `null` when there's no signal state yet (bot hasn't ticked
 * today) or the limit is 0/unset (nothing meaningful to show). */
export function computeDailyLossProgress(
  dailyPnl: string | number | null | undefined,
  dailyLossLimit: string | number | null | undefined
): DailyLossProgress | null {
  const limit = toNumber(dailyLossLimit);
  if (!limit || dailyPnl === null || dailyPnl === undefined || dailyPnl === "") return null;
  const pnl = toNumber(dailyPnl);
  const loss = Math.max(0, -pnl);
  const usedPct = (loss / limit) * 100;
  const severity: DailyLossProgress["severity"] =
    usedPct >= 100 ? "critical" : usedPct >= 70 ? "warning" : "ok";
  return { usedPct, dailyPnl: pnl, limit, severity };
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
