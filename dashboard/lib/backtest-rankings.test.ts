import { describe, expect, it } from "vitest";
import { passesGuardrails, rankByCriterion, rankByComposite } from "./backtest-rankings";
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
