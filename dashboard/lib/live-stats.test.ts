import { describe, expect, it } from "vitest";
import { computeLiveStats, flagDrift } from "./live-stats";
import type { BacktestRun, PaperOrder } from "./types";

function order(overrides: Partial<PaperOrder>): PaperOrder {
  return {
    id: "o1",
    paper_position_id: "p1",
    side: "sell",
    quantity: "1",
    simulated_fill_price: "100",
    filled_at: "2026-01-01T00:00:00Z",
    pnl: "100",
    close_reason: "strategy_signal",
    ...overrides,
  };
}

function backtestRun(overrides: Partial<BacktestRun>): BacktestRun {
  return {
    id: "r1",
    strategy_id: "s1",
    instrument_id: "i1",
    started_at: "2026-01-01T00:00:00Z",
    period_start: "2021-01-01T00:00:00Z",
    period_end: "2026-01-01T00:00:00Z",
    sharpe_ratio: "1.0",
    max_drawdown_pct: "20",
    win_rate_pct: "70",
    profit_factor: "2.0",
    cagr_pct: "20",
    ...overrides,
  };
}

describe("computeLiveStats", () => {
  it("computes win rate from sell fills only, ignoring buys", () => {
    const orders = [
      order({ side: "buy", pnl: null }),
      order({ side: "sell", pnl: "500" }),
      order({ side: "sell", pnl: "-200" }),
      order({ side: "sell", pnl: "300" }),
    ];
    const stats = computeLiveStats(orders);
    expect(stats.tradeCount).toBe(3);
    expect(stats.winRatePct).toBeCloseTo((2 / 3) * 100, 5);
  });

  it("returns zero trades/win rate when there are no sell fills yet", () => {
    const stats = computeLiveStats([order({ side: "buy", pnl: null })]);
    expect(stats).toEqual({ tradeCount: 0, winRatePct: 0 });
  });
});

describe("flagDrift", () => {
  it("flags a real divergence once there are enough trades", () => {
    const stats = { tradeCount: 10, winRatePct: 30 };
    const run = backtestRun({ win_rate_pct: "77" });
    const flag = flagDrift(stats, run);
    expect(flag).not.toBeNull();
    expect(flag!.deltaPct).toBeCloseTo(30 - 77, 5);
  });

  it("stays silent below the minimum trade count, however large the gap", () => {
    const stats = { tradeCount: 2, winRatePct: 0 };
    const run = backtestRun({ win_rate_pct: "100" });
    expect(flagDrift(stats, run)).toBeNull();
  });

  it("stays silent when live tracks the backtest closely", () => {
    const stats = { tradeCount: 20, winRatePct: 72 };
    const run = backtestRun({ win_rate_pct: "77" });
    expect(flagDrift(stats, run)).toBeNull();
  });

  it("stays silent when there's no matching backtest run", () => {
    const stats = { tradeCount: 20, winRatePct: 10 };
    expect(flagDrift(stats, undefined)).toBeNull();
  });
});
