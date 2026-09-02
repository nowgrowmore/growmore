import { afterEach, describe, expect, it, vi } from "vitest";
import {
  __setTestClient,
  getBotConfigs,
  getOpenPaperPositions,
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

describe("updateBotConfigRiskParams", () => {
  it("runs the update and an audit_log insert inside one transaction", async () => {
    const fakeSql = makeFakeSql([]);
    __setTestClient(fakeSql as never);

    await updateBotConfigRiskParams("config-1", {
      maxPositionSize: 10,
      dailyLossLimit: 5000,
      virtualCapital: 100000,
    });

    expect(fakeSql.transaction).toHaveBeenCalledTimes(1);
    expect(fakeSql).toHaveBeenCalledTimes(2);
  });
});
