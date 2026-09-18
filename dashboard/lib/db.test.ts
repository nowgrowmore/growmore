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
  getLotSizesBySymbol,
  getMCXOptionsConfigs,
  getMCXOptionsLegsForConfigs,
  getMCXOptionsPositionsForConfigs,
  getMCXOptionsSelectionsForConfigs,
  getWheelBasketConfigs,
  getWheelBasketLegs,
  getWheelBasketPositions,
  getWheelBasketSelections,
  setBotConfigEnabled,
  setMCXOptionsConfigEnabled,
  setWheelBasketConfigEnabled,
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

describe("getWheelBasketConfigs", () => {
  it("returns whatever rows the client resolves with", async () => {
    const fakeRows = [{ id: "config-1", enabled: true }];
    const fakeSql = makeFakeSql(fakeRows);
    __setTestClient(fakeSql as never);

    const result = await getWheelBasketConfigs();

    expect(result).toBe(fakeRows);
  });
});

describe("getWheelBasketPositions", () => {
  it("returns the positions for one config", async () => {
    const fakeRows = [{ id: "pos-1", symbol: "RELIANCE" }];
    const fakeSql = makeFakeSql(fakeRows);
    __setTestClient(fakeSql as never);

    const result = await getWheelBasketPositions("config-1");

    expect(result).toBe(fakeRows);
  });
});

describe("getWheelBasketLegs", () => {
  it("returns the legs joined through positions for one config", async () => {
    const fakeRows = [{ id: "leg-1", action: "sell_put" }];
    const fakeSql = makeFakeSql(fakeRows);
    __setTestClient(fakeSql as never);

    const result = await getWheelBasketLegs("config-1");

    expect(result).toBe(fakeRows);
  });
});

describe("getWheelBasketSelections", () => {
  it("returns the selection log for one config", async () => {
    const fakeRows = [{ id: "sel-1", symbol: "RELIANCE", selected: true }];
    const fakeSql = makeFakeSql(fakeRows);
    __setTestClient(fakeSql as never);

    const result = await getWheelBasketSelections("config-1");

    expect(result).toBe(fakeRows);
  });
});

describe("setWheelBasketConfigEnabled", () => {
  it("runs the update and an audit_log insert inside one transaction", async () => {
    const fakeSql = makeFakeSql([]);
    __setTestClient(fakeSql as never);

    await setWheelBasketConfigEnabled("config-1", true);

    expect(fakeSql.transaction).toHaveBeenCalledTimes(1);
    expect(fakeSql).toHaveBeenCalledTimes(2);
  });

  it("records the requested enabled value in the audit payload", async () => {
    const fakeSql = makeFakeSql([]);
    __setTestClient(fakeSql as never);

    await setWheelBasketConfigEnabled("config-1", false);

    const auditCallParams = fakeSql.calls[1];
    expect(JSON.stringify(auditCallParams)).toContain("wheel_basket_disabled");
  });
});

describe("getMCXOptionsConfigs", () => {
  it("returns whatever rows the client resolves with", async () => {
    const fakeRows = [{ id: "config-1", enabled: true, symbol: "GOLDM" }];
    const fakeSql = makeFakeSql(fakeRows);
    __setTestClient(fakeSql as never);

    const result = await getMCXOptionsConfigs();

    expect(result).toBe(fakeRows);
  });
});

// These three used to be per-config getters, which the /mcx-options page
// called in a 3N + 1 fan-out under `force-dynamic` with a 60s auto-refresh.
// They are now batched over every config id at once -- so each one also has
// to key its rows back to the right config, which is the part worth testing
// (an off-by-one in the old index-zipping would have silently shown GOLDM's
// positions under SILVERM).
describe("getMCXOptionsPositionsForConfigs", () => {
  it("groups the positions by config_id", async () => {
    const fakeRows = [
      { id: "pos-1", config_id: "config-1", state: "long_futures" },
      { id: "pos-2", config_id: "config-2", state: "flat" },
      { id: "pos-3", config_id: "config-1", state: "closed" },
    ];
    const fakeSql = makeFakeSql(fakeRows);
    __setTestClient(fakeSql as never);

    const result = await getMCXOptionsPositionsForConfigs(["config-1", "config-2"]);

    expect(result["config-1"].map((p) => p.id)).toEqual(["pos-1", "pos-3"]);
    expect(result["config-2"].map((p) => p.id)).toEqual(["pos-2"]);
  });

  it("returns an empty bucket per config, and never queries, with no configs", async () => {
    const fakeSql = makeFakeSql([]);
    __setTestClient(fakeSql as never);

    expect(await getMCXOptionsPositionsForConfigs([])).toEqual({});
    expect(fakeSql).not.toHaveBeenCalled();
  });

  it("gives a config with no rows an empty array rather than undefined", async () => {
    const fakeSql = makeFakeSql([{ id: "pos-1", config_id: "config-1" }]);
    __setTestClient(fakeSql as never);

    const result = await getMCXOptionsPositionsForConfigs(["config-1", "config-2"]);

    expect(result["config-2"]).toEqual([]);
  });
});

describe("getMCXOptionsLegsForConfigs", () => {
  it("groups the legs by the config_id joined through positions", async () => {
    const fakeRows = [
      { id: "leg-1", config_id: "config-1", action: "sell_put" },
      { id: "leg-2", config_id: "config-2", action: "roll" },
    ];
    const fakeSql = makeFakeSql(fakeRows);
    __setTestClient(fakeSql as never);

    const result = await getMCXOptionsLegsForConfigs(["config-1", "config-2"]);

    expect(result["config-1"].map((l) => l.id)).toEqual(["leg-1"]);
    expect(result["config-2"].map((l) => l.id)).toEqual(["leg-2"]);
  });
});

describe("getMCXOptionsSelectionsForConfigs", () => {
  it("groups the selection log by config_id", async () => {
    const fakeRows = [
      { id: "sel-1", config_id: "config-1", regime: "consolidating" },
      { id: "sel-2", config_id: "config-1", regime: "trend_unfavorable" },
    ];
    const fakeSql = makeFakeSql(fakeRows);
    __setTestClient(fakeSql as never);

    const result = await getMCXOptionsSelectionsForConfigs(["config-1"]);

    expect(result["config-1"].map((s) => s.id)).toEqual(["sel-1", "sel-2"]);
  });

  it("trims a huge candidates_considered blob but keeps the true total", async () => {
    // The first real production cycles evaluated 144 (GOLDM) and 176
    // (SILVERM) strikes. Shipping 300 such rows to render ten entries each
    // was most of this page's payload.
    const many = Array.from({ length: 144 }, (_, i) => ({
      strike: 200000 + i * 500,
      delta: -0.5 + i * 0.002,
      oi: 100 + i,
      ltp: 50 + i,
    }));
    const fakeSql = makeFakeSql([
      {
        id: "sel-1",
        config_id: "config-1",
        selected_strike: "207000",
        target_delta: "0.30",
        candidates_considered: many,
      },
    ]);
    __setTestClient(fakeSql as never);

    const [row] = (await getMCXOptionsSelectionsForConfigs(["config-1"]))["config-1"];

    expect(row.candidates_considered!.length).toBeLessThan(many.length);
    expect(row.candidates_total).toBe(144);
  });

  it("leaves a small candidate list untouched", async () => {
    const few = [{ strike: 207000, delta: -0.3, oi: 500, ltp: 42 }];
    const fakeSql = makeFakeSql([
      { id: "sel-1", config_id: "config-1", selected_strike: null, target_delta: null,
        candidates_considered: few },
    ]);
    __setTestClient(fakeSql as never);

    const [row] = (await getMCXOptionsSelectionsForConfigs(["config-1"]))["config-1"];

    expect(row.candidates_considered).toEqual(few);
    expect(row.candidates_total).toBe(1);
  });
});

describe("setMCXOptionsConfigEnabled", () => {
  it("runs the update and an audit_log insert inside one transaction", async () => {
    const fakeSql = makeFakeSql([]);
    __setTestClient(fakeSql as never);

    await setMCXOptionsConfigEnabled("config-1", true);

    expect(fakeSql.transaction).toHaveBeenCalledTimes(1);
    expect(fakeSql).toHaveBeenCalledTimes(2);
  });

  it("records the requested enabled value in the audit payload", async () => {
    const fakeSql = makeFakeSql([]);
    __setTestClient(fakeSql as never);

    await setMCXOptionsConfigEnabled("config-1", false);

    const auditCallParams = fakeSql.calls[1];
    expect(JSON.stringify(auditCallParams)).toContain("mcx_options_disabled");
  });
});

describe("getLotSizesBySymbol", () => {
  it("keys the contract multiplier by symbol", async () => {
    // GOLDM quotes per 10g on a 100g lot and SILVERM per kg on a 5kg lot, so
    // exposure is wrong by 10x / 5x without this.
    const fakeSql = makeFakeSql([
      { symbol: "GOLDM", lot_size: 10 },
      { symbol: "SILVERM", lot_size: 5 },
    ]);
    __setTestClient(fakeSql as never);

    expect(await getLotSizesBySymbol()).toEqual({ GOLDM: 10, SILVERM: 5 });
  });
});
