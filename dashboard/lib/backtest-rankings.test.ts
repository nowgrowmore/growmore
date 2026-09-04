import { describe, expect, it } from "vitest";
import {
  findMatchingBacktestRun,
  findRankPositions,
  passesGuardrails,
  rankByCriterion,
  rankByComposite,
} from "./backtest-rankings";
import type { BacktestRun } from "./types";

function makeRun(overrides: Partial<BacktestRun>): BacktestRun {
  return {
    id: "r1",
    strategy_id: "s1",
    instrument_id: "i1",
    started_at: "2026-01-01T00:00:00Z",
    period_start: "2021-01-01T00:00:00Z",
    period_end: "2026-01-01T00:00:00Z",
    sharpe_ratio: "1.0",
    max_drawdown_pct: "20",
    win_rate_pct: "50",
    profit_factor: "2.0",
    cagr_pct: "20",
    trade_count: "30",
    ...overrides,
  };
}

describe("passesGuardrails", () => {
  it("passes a run with enough trades and acceptable drawdown", () => {
    const run = makeRun({ trade_count: "20", max_drawdown_pct: "30" });
    expect(passesGuardrails(run)).toBe(true);
  });

  it("fails a run with too few trades", () => {
    const run = makeRun({ trade_count: "5", max_drawdown_pct: "10" });
    expect(passesGuardrails(run)).toBe(false);
  });

  it("fails a run with drawdown over the guardrail", () => {
    const run = makeRun({ trade_count: "50", max_drawdown_pct: "55" });
    expect(passesGuardrails(run)).toBe(false);
  });

  it("respects custom thresholds", () => {
    const run = makeRun({ trade_count: "10", max_drawdown_pct: "40" });
    expect(passesGuardrails(run, { minTradeCount: 5, maxDrawdownPct: 50 })).toBe(true);
    expect(passesGuardrails(run, { minTradeCount: 15, maxDrawdownPct: 50 })).toBe(false);
  });
});

describe("rankByCriterion", () => {
  const runs = [
    makeRun({ id: "a", cagr_pct: "10", max_drawdown_pct: "10", trade_count: "20" }),
    makeRun({ id: "b", cagr_pct: "30", max_drawdown_pct: "40", trade_count: "20" }),
    makeRun({ id: "c", cagr_pct: "20", max_drawdown_pct: "20", trade_count: "20" }),
  ];

  it("ranks descending for a higher-is-better metric like CAGR", () => {
    const ranked = rankByCriterion(runs, "cagr_pct");
    expect(ranked.map((r) => r.id)).toEqual(["b", "c", "a"]);
  });

  it("ranks ascending for max_drawdown_pct (lower is better)", () => {
    const ranked = rankByCriterion(runs, "max_drawdown_pct");
    expect(ranked.map((r) => r.id)).toEqual(["a", "c", "b"]);
  });

  it("excludes runs that fail the guardrails", () => {
    const withThin = [...runs, makeRun({ id: "thin", cagr_pct: "1000", trade_count: "2" })];
    const ranked = rankByCriterion(withThin, "cagr_pct");
    expect(ranked.map((r) => r.id)).not.toContain("thin");
  });

  it("respects a limit", () => {
    const ranked = rankByCriterion(runs, "cagr_pct", 2);
    expect(ranked).toHaveLength(2);
  });
});

describe("rankByComposite", () => {
  it("ranks a run strong in both CAGR and Sharpe above one strong in only one", () => {
    const runs = [
      makeRun({ id: "both-strong", cagr_pct: "50", sharpe_ratio: "2.0", trade_count: "20" }),
      makeRun({ id: "cagr-only", cagr_pct: "80", sharpe_ratio: "0.2", trade_count: "20" }),
      makeRun({ id: "sharpe-only", cagr_pct: "5", sharpe_ratio: "3.0", trade_count: "20" }),
    ];
    const ranked = rankByComposite(runs);
    expect(ranked[0].id).toBe("both-strong");
  });

  it("excludes runs that fail the guardrails", () => {
    const runs = [
      makeRun({ id: "ok", cagr_pct: "20", sharpe_ratio: "1", trade_count: "20" }),
      makeRun({ id: "thin", cagr_pct: "1000", sharpe_ratio: "10", trade_count: "2" }),
    ];
    const ranked = rankByComposite(runs);
    expect(ranked.map((r) => r.id)).toEqual(["ok"]);
  });
});

describe("findMatchingBacktestRun", () => {
  it("finds the run matching a config's strategy+instrument pair", () => {
    const runs = [
      makeRun({ id: "wrong-strategy", strategy_id: "other", instrument_id: "i1" }),
      makeRun({ id: "match", strategy_id: "s1", instrument_id: "i1" }),
      makeRun({ id: "wrong-instrument", strategy_id: "s1", instrument_id: "other" }),
    ];
    const found = findMatchingBacktestRun({ strategy_id: "s1", instrument_id: "i1" }, runs);
    expect(found?.id).toBe("match");
  });

  it("picks the most recently started run when there are multiple", () => {
    const runs = [
      makeRun({ id: "older", strategy_id: "s1", instrument_id: "i1", started_at: "2025-01-01T00:00:00Z" }),
      makeRun({ id: "newer", strategy_id: "s1", instrument_id: "i1", started_at: "2026-01-01T00:00:00Z" }),
    ];
    const found = findMatchingBacktestRun({ strategy_id: "s1", instrument_id: "i1" }, runs);
    expect(found?.id).toBe("newer");
  });

  it("returns undefined when no run matches", () => {
    const found = findMatchingBacktestRun({ strategy_id: "s1", instrument_id: "i1" }, []);
    expect(found).toBeUndefined();
  });
});

describe("findRankPositions", () => {
  it("reports the 1-based rank for each criterion the run places in the top N for", () => {
    const runs = [
      makeRun({ id: "best", cagr_pct: "50", sharpe_ratio: "2.0", max_drawdown_pct: "10", trade_count: "20" }),
      makeRun({ id: "middle", cagr_pct: "20", sharpe_ratio: "1.0", max_drawdown_pct: "20", trade_count: "20" }),
      makeRun({ id: "worst", cagr_pct: "5", sharpe_ratio: "0.5", max_drawdown_pct: "40", trade_count: "20" }),
    ];
    const positions = findRankPositions(runs[1], runs); // "middle"
    const byLabel = Object.fromEntries(positions.map((p) => [p.label, p.rank]));
    expect(byLabel["CAGR"]).toBe(2);
    expect(byLabel["Sharpe"]).toBe(2);
    expect(byLabel["Max drawdown"]).toBe(2);
  });

  it("omits a criterion when the run falls outside the top N", () => {
    const runs = Array.from({ length: 12 }, (_, i) =>
      makeRun({ id: `r${i}`, cagr_pct: String(100 - i), trade_count: "20" })
    );
    const last = runs[runs.length - 1]; // rank 12th on CAGR -- outside top 10
    const positions = findRankPositions(last, runs, 10);
    expect(positions.find((p) => p.label === "CAGR")).toBeUndefined();
  });

  it("returns an empty list when the run is undefined (config never backtested)", () => {
    expect(findRankPositions(undefined, [])).toEqual([]);
  });
});
