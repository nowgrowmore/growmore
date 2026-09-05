import { toNumber } from "./format";
import type { BacktestRun, LiveOrder, PaperOrder } from "./types";

/** Real win rate / trade count for one bot_config, computed from its own
 * sell fills -- "win" defined identically to the backtest's own
 * `win_rate_pct` (bot/growmore_bot/backtest/metrics.py: strictly positive
 * pnl), so the comparison in flagDrift is apples-to-apples. */
export interface LiveStats {
  tradeCount: number;
  winRatePct: number;
}

export function computeLiveStats(orders: (PaperOrder | LiveOrder)[]): LiveStats {
  const sells = orders.filter((o) => o.side === "sell" && o.pnl !== null);
  const tradeCount = sells.length;
  if (tradeCount === 0) return { tradeCount: 0, winRatePct: 0 };
  const wins = sells.filter((o) => toNumber(o.pnl) > 0).length;
  return { tradeCount, winRatePct: (wins / tradeCount) * 100 };
}

const MIN_TRADES_TO_FLAG = 5;
const DRIFT_THRESHOLD_PCT = 25;

export interface DriftFlag {
  liveWinRatePct: number;
  backtestWinRatePct: number;
  deltaPct: number;
}

/** Only flags a real, actionable divergence -- silent (null) below the
 * minimum sample size (a handful of trades proves nothing either way) or
 * when live tracks the backtest closely enough not to be worth a second
 * look. */
export function flagDrift(liveStats: LiveStats, backtestRun: BacktestRun | undefined): DriftFlag | null {
  if (!backtestRun || liveStats.tradeCount < MIN_TRADES_TO_FLAG) return null;
  const backtestWinRatePct = toNumber(backtestRun.win_rate_pct);
  const deltaPct = liveStats.winRatePct - backtestWinRatePct;
  if (Math.abs(deltaPct) < DRIFT_THRESHOLD_PCT) return null;
  return { liveWinRatePct: liveStats.winRatePct, backtestWinRatePct, deltaPct };
}
