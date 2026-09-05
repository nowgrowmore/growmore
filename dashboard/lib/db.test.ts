import { afterEach, describe, expect, it, vi } from "vitest";
import {
  __setTestClient,
  getAllLivePositions,
  getAllPaperPositions,
  getAuditLog,
  getBotConfigs,
  getBotStatus,
  getLastConfigStateChange,
  getLastConfigStateChangeForConfigs,
  getOpenPaperPositions,
  getPortfolioBacktestRuns,
  getPortfolioEquityCurve,
  getPortfolioHoldings,
  getRecentSignals,
  getRecentSignalsForConfigs,
  setBotConfigEnabled,
  updateBotConfigRiskParams,
} from "./db";

// These tests never touch a real Postgres connection — a fake `sql` tagged
// template function (matching the SqlClient shape lib/db.ts wraps the
// `postgres` package's client in) is injected via the __setTestClient test
// seam. This keeps lib/db.ts's query-building logic covered without
// requiring a live DB, per the project's "mock the DB layer for unit tests"
// convention.

function makeFakeSql(rows: unknown[] = []) {
  const calls: unknown[][] = [];
  const tag = vi.fn((strings: TemplateStringsArray, ...params: unknown[]) => {
    calls.push(params);
    return Promise.resolve(rows);
  }) as unknown as {
    (strings: TemplateStringsArray, ...params: unknown[]): Promise<unknown[]>;
    transaction: (
      queriesOrFn: Promise<unknown>[] | ((tx: typeof tag) => Promise<unknown>[])
    ) => Promise<unknown[]>;
    calls: unknown[][];
  };
  tag.transaction = vi.fn(async (queriesOrFn) => {
    const queries = typeof queriesOrFn === "function" ? queriesOrFn(tag) : queriesOrFn;
    return Promise.all(queries);
  });
  tag.calls = calls;
  return tag;
}

afterEach(() => {
  __setTestClient(null);
});

describe("getOpenPaperPositions", () => {
  it("returns whatever rows the client resolves with", async () => {
    const fakeRows = [{ id: "p1", status: "open" }];
    const fakeSql = makeFakeSql(fakeRows);
    __setTestClient(fakeSql as never);

    const result = await getOpenPaperPositions();

    expect(result).toBe(fakeRows);
    expect(fakeSql).toHaveBeenCalledTimes(1);
  });
});

describe("getAllPaperPositions", () => {
  it("joins bot_config filtered to mode='paper', so a live-mode config sharing the same " +
     "strategy+instrument never duplicates the row via a fan-out join", async () => {
    const fakeRows = [{ id: "p1", status: "open" }];
    const fakeSql = makeFakeSql(fakeRows);
    __setTestClient(fakeSql as never);

    const result = await getAllPaperPositions();

    expect(result).toBe(fakeRows);
    type MockFn = { mock: { calls: unknown[][] } };
    const queryText = ((fakeSql as unknown as MockFn).mock.calls[0][0] as string[]).join("");
    expect(queryText).toContain("mode = 'paper'");
  });
});

describe("getAllLivePositions", () => {
  it("joins bot_config filtered to mode='live', mirroring the identical fix on the paper side", async () => {
    const fakeRows = [{ id: "p1", status: "open" }];
    const fakeSql = makeFakeSql(fakeRows);
    __setTestClient(fakeSql as never);

    const result = await getAllLivePositions();

    expect(result).toBe(fakeRows);
    type MockFn = { mock: { calls: unknown[][] } };
    const queryText = ((fakeSql as unknown as MockFn).mock.calls[0][0] as string[]).join("");
    expect(queryText).toContain("mode = 'live'");
  });
});

describe("getRecentSignals", () => {
  it("returns rows in oldest-first order for the sparkline", async () => {
    // The query orders by checked_at DESC (most efficient for LIMIT), so the
    // function must reverse before returning -- oldest-to-newest is what a
    // left-to-right sparkline needs.
    const newestFirst = [
      { id: "3", action: "BUY", checked_at: "2026-01-03T00:00:00Z" },
      { id: "2", action: "HOLD", checked_at: "2026-01-02T00:00:00Z" },
      { id: "1", action: "HOLD", checked_at: "2026-01-01T00:00:00Z" },
    ];
    const fakeSql = makeFakeSql(newestFirst);
    __setTestClient(fakeSql as never);

    const result = await getRecentSignals("config-1", 5);

    expect(result.map((r) => r.id)).toEqual(["1", "2", "3"]);
  });
});

describe("getRecentSignalsForConfigs", () => {
  it("groups rows by bot_config_id, each oldest-first", async () => {
    const rows = [
      { id: "1", bot_config_id: "a", action: "HOLD", checked_at: "2026-01-01T00:00:00Z" },
      { id: "2", bot_config_id: "a", action: "BUY", checked_at: "2026-01-02T00:00:00Z" },
      { id: "3", bot_config_id: "b", action: "SELL", checked_at: "2026-01-01T00:00:00Z" },
    ];
    const fakeSql = makeFakeSql(rows);
    __setTestClient(fakeSql as never);

    const result = await getRecentSignalsForConfigs(["a", "b"]);

    expect(result["a"].map((r) => r.id)).toEqual(["1", "2"]);
    expect(result["b"].map((r) => r.id)).toEqual(["3"]);
  });

  it("includes an empty array for a config with no history yet", async () => {
    const fakeSql = makeFakeSql([]);
    __setTestClient(fakeSql as never);

    const result = await getRecentSignalsForConfigs(["a"]);

    expect(result).toEqual({ a: [] });
  });

  it("returns an empty map without querying when given no ids", async () => {
    const fakeSql = makeFakeSql([]);
    __setTestClient(fakeSql as never);

    const result = await getRecentSignalsForConfigs([]);

    expect(result).toEqual({});
    expect(fakeSql).not.toHaveBeenCalled();
  });
});

describe("getLastConfigStateChangeForConfigs", () => {
  it("groups the most recent matching row by bot_config_id", async () => {
    const rows = [
      { id: "a1", event_type: "strategy_disabled", payload: { bot_config_id: "c1" } },
      { id: "a2", event_type: "strategy_enabled", payload: { bot_config_id: "c2" } },
    ];
    const fakeSql = makeFakeSql(rows);
    __setTestClient(fakeSql as never);

    const result = await getLastConfigStateChangeForConfigs(["c1", "c2"]);

    expect(result["c1"].id).toBe("a1");
    expect(result["c2"].id).toBe("a2");
  });

  it("returns an empty map without querying when given no ids", async () => {
    const fakeSql = makeFakeSql([]);
    __setTestClient(fakeSql as never);

    const result = await getLastConfigStateChangeForConfigs([]);

    expect(result).toEqual({});
    expect(fakeSql).not.toHaveBeenCalled();
  });
});

describe("getLastConfigStateChange", () => {
  it("returns the matching audit_log row when one exists", async () => {
    const fakeRows = [{ id: "a1", event_type: "strategy_disabled", payload: { bot_config_id: "c1" } }];
    const fakeSql = makeFakeSql(fakeRows);
    __setTestClient(fakeSql as never);

    const result = await getLastConfigStateChange("c1");

    expect(result).toEqual(fakeRows[0]);
  });

  it("returns null when no matching row exists (e.g. predates the bot_config_id payload fix)", async () => {
    const fakeSql = makeFakeSql([]);
    __setTestClient(fakeSql as never);

    const result = await getLastConfigStateChange("c1");

    expect(result).toBeNull();
  });
});

describe("getBotConfigs", () => {
  it("queries bot_config joined with strategy/instrument names", async () => {
    const fakeRows = [{ id: "c1", enabled: true }];
    const fakeSql = makeFakeSql(fakeRows);
    __setTestClient(fakeSql as never);

    const result = await getBotConfigs();

    expect(result).toBe(fakeRows);
    expect(fakeSql).toHaveBeenCalledTimes(1);
  });
});

describe("setBotConfigEnabled", () => {
  it("runs the update and an audit_log insert inside one transaction", async () => {
    const fakeSql = makeFakeSql([]);
    __setTestClient(fakeSql as never);

    await setBotConfigEnabled("config-1", true);

    expect(fakeSql.transaction).toHaveBeenCalledTimes(1);
    // The transaction callback issues exactly two statements: the update and
    // the audit_log insert.
    expect(fakeSql).toHaveBeenCalledTimes(2);
  });

  it("records the requested enabled value in the audit payload", async () => {
    const fakeSql = makeFakeSql([]);
    __setTestClient(fakeSql as never);

    await setBotConfigEnabled("config-1", false);

    const auditCallParams = fakeSql.calls[1];
    expect(JSON.stringify(auditCallParams)).toContain("strategy_disabled");
  });
});

describe("getBotStatus", () => {
  it("returns the singleton row when one exists", async () => {
    const fakeRows = [{ id: "status-1", live_trading_enabled: true }];
    const fakeSql = makeFakeSql(fakeRows);
    __setTestClient(fakeSql as never);

    const result = await getBotStatus();

    expect(result).toBe(fakeRows[0]);
  });

  it("returns null when no row exists yet", async () => {
    const fakeSql = makeFakeSql([]);
    __setTestClient(fakeSql as never);

    const result = await getBotStatus();

    expect(result).toBeNull();
  });
});

describe("getAuditLog", () => {
  it("returns the audit_log rows", async () => {
    const fakeRows = [{ id: "log-1", event_type: "strategy_enabled" }];
    const fakeSql = makeFakeSql(fakeRows);
    __setTestClient(fakeSql as never);

    const result = await getAuditLog();

    expect(result).toBe(fakeRows);
    expect(fakeSql).toHaveBeenCalledTimes(1);
  });
});

describe("updateBotConfigRiskParams", () => {
  it("runs the update and an audit_log insert inside one transaction", async () => {
    const fakeSql = makeFakeSql([]);
    __setTestClient(fakeSql as never);

    await updateBotConfigRiskParams("config-1", {
      maxPositionSize: 10,
      dailyLossLimit: 5000,
      virtualCapital: 100000,
      dailyLossLimitEnabled: true,
    });

    expect(fakeSql.transaction).toHaveBeenCalledTimes(1);
    expect(fakeSql).toHaveBeenCalledTimes(2);
  });
});

describe("getPortfolioBacktestRuns", () => {
  it("returns the portfolio_backtest_runs rows", async () => {
    const fakeRows = [{ id: "run-1", universe: "smallcap250" }];
    const fakeSql = makeFakeSql(fakeRows);
    __setTestClient(fakeSql as never);

    const result = await getPortfolioBacktestRuns();

    expect(result).toBe(fakeRows);
    expect(fakeSql).toHaveBeenCalledTimes(1);
  });
});

describe("getPortfolioEquityCurve", () => {
  it("returns the equity curve points for one run", async () => {
    const fakeRows = [{ id: "point-1", equity: "1000000" }];
    const fakeSql = makeFakeSql(fakeRows);
    __setTestClient(fakeSql as never);

    const result = await getPortfolioEquityCurve("run-1");

    expect(result).toBe(fakeRows);
  });
});

describe("getPortfolioHoldings", () => {
  it("returns the rebalance holdings for one run", async () => {
    const fakeRows = [{ id: "holding-1", symbol: "TTML" }];
    const fakeSql = makeFakeSql(fakeRows);
    __setTestClient(fakeSql as never);

    const result = await getPortfolioHoldings("run-1");

    expect(result).toBe(fakeRows);
  });
});
