import { describe, expect, it } from "vitest";
import type { BacktestRun, PaperPosition } from "./types";
import {
  filterBacktestRuns,
  formatCurrency,
  formatPercent,
  formatNumber,
  sortBacktestRuns,
  summarizePositions,
  toNumber,
} from "./format";

function makePosition(overrides: Partial<PaperPosition>): PaperPosition {
  return {
    id: "pos-1",
    strategy_id: "strat-1",
    instrument_id: "inst-1",
    status: "open",
    quantity: "1",
    avg_entry_price: "100",
    realized_pnl: "0",
    unrealized_pnl: "0",
    opened_at: "2026-01-01T00:00:00Z",
    closed_at: null,
    ...overrides,
  };
}

function makeRun(overrides: Partial<BacktestRun>): BacktestRun {
  return {
    id: "run-1",
    strategy_id: "strat-1",
    instrument_id: "inst-1",
    started_at: "2026-01-01T00:00:00Z",
    period_start: "2025-01-01T00:00:00Z",
    period_end: "2026-01-01T00:00:00Z",
    sharpe_ratio: "1.0",
    max_drawdown_pct: "10.0",
    win_rate_pct: "50.0",
    profit_factor: "1.5",
    cagr_pct: "12.0",
    ...overrides,
  };
}

describe("toNumber", () => {
  it("parses a numeric string", () => {
    expect(toNumber("123.45")).toBe(123.45);
  });

  it("defaults null/undefined/empty to 0", () => {
    expect(toNumber(null)).toBe(0);
    expect(toNumber(undefined)).toBe(0);
    expect(toNumber("")).toBe(0);
  });

  it("defaults non-numeric strings to 0", () => {
    expect(toNumber("not-a-number")).toBe(0);
  });
});

describe("formatCurrency", () => {
  it("formats a positive value in INR", () => {
    expect(formatCurrency(1000)).toContain("1,000");
  });

  it("prefixes negative values with a minus sign", () => {
    expect(formatCurrency(-500).startsWith("-")).toBe(true);
  });

  it("adds an explicit + sign when signDisplay is requested", () => {
    expect(formatCurrency(500, { signDisplay: true }).startsWith("+")).toBe(true);
  });
});

describe("formatPercent / formatNumber", () => {
  it("renders an em dash for null/undefined/NaN", () => {
    expect(formatPercent(null)).toBe("—");
    expect(formatPercent(undefined)).toBe("—");
    expect(formatNumber(NaN)).toBe("—");
  });

  it("formats to the requested precision", () => {
    expect(formatPercent(12.3456, 2)).toBe("12.35%");
    expect(formatNumber(1.005, 1)).toMatch(/^1\.0/);
  });
});

describe("summarizePositions", () => {
  it("counts only open positions but sums P&L across all", () => {
    const positions = [
      makePosition({ id: "1", status: "open", unrealized_pnl: "150.5", realized_pnl: "0" }),
      makePosition({ id: "2", status: "open", unrealized_pnl: "-50", realized_pnl: "0" }),
      makePosition({
        id: "3",
        status: "closed",
        unrealized_pnl: "0",
        realized_pnl: "300",
        closed_at: "2026-01-02T00:00:00Z",
      }),
    ];

    const summary = summarizePositions(positions);

    expect(summary.openPositionCount).toBe(2);
    expect(summary.totalUnrealizedPnl).toBeCloseTo(100.5);
    expect(summary.totalRealizedPnl).toBeCloseTo(300);
  });

  it("returns zeros for an empty list", () => {
    expect(summarizePositions([])).toEqual({
      openPositionCount: 0,
      totalUnrealizedPnl: 0,
      totalRealizedPnl: 0,
    });
  });
});

describe("sortBacktestRuns", () => {
  const runs = [
    makeRun({ id: "a", sharpe_ratio: "0.5" }),
    makeRun({ id: "b", sharpe_ratio: "2.0" }),
    makeRun({ id: "c", sharpe_ratio: "1.0" }),
  ];

  it("sorts descending by default", () => {
    expect(sortBacktestRuns(runs, "sharpe_ratio").map((r) => r.id)).toEqual(["b", "c", "a"]);
  });

  it("sorts ascending when requested", () => {
    expect(sortBacktestRuns(runs, "sharpe_ratio", "asc").map((r) => r.id)).toEqual([
      "a",
      "c",
      "b",
    ]);
  });

  it("does not mutate the input array", () => {
    const copy = [...runs];
    sortBacktestRuns(runs, "sharpe_ratio");
    expect(runs).toEqual(copy);
  });
});

describe("filterBacktestRuns", () => {
  const runs = [
    makeRun({ id: "a", strategy_id: "s1", instrument_id: "i1" }),
    makeRun({ id: "b", strategy_id: "s2", instrument_id: "i1" }),
    makeRun({ id: "c", strategy_id: "s1", instrument_id: "i2" }),
  ];

  it("filters by strategyId and instrumentId independently", () => {
    expect(filterBacktestRuns(runs, { strategyId: "s1" }).map((r) => r.id)).toEqual(["a", "c"]);
    expect(filterBacktestRuns(runs, { instrumentId: "i1" }).map((r) => r.id)).toEqual(["a", "b"]);
  });

  it("combines both filters", () => {
    expect(
      filterBacktestRuns(runs, { strategyId: "s1", instrumentId: "i2" }).map((r) => r.id)
    ).toEqual(["c"]);
  });

  it("returns all rows when no filters are given", () => {
    expect(filterBacktestRuns(runs, {})).toHaveLength(3);
  });
});
