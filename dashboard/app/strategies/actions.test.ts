import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock the DB layer and Next's cache revalidation so this test exercises
// only the server action's own logic (form parsing, validation, wiring the
// write call to the audit-logging db functions) — no DB connection, no
// Next.js runtime needed.
vi.mock("@/lib/db", () => ({
  setBotConfigEnabled: vi.fn(),
  updateBotConfigRiskParams: vi.fn(),
}));
vi.mock("next/cache", () => ({
  revalidatePath: vi.fn(),
}));

import { setBotConfigEnabled, updateBotConfigRiskParams } from "@/lib/db";
import { revalidatePath } from "next/cache";
import { saveRiskParams, toggleBotConfigEnabled } from "./actions";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("toggleBotConfigEnabled", () => {
  it("delegates to setBotConfigEnabled and revalidates the strategies page", async () => {
    await toggleBotConfigEnabled("config-1", true);

    expect(setBotConfigEnabled).toHaveBeenCalledWith("config-1", true);
    expect(revalidatePath).toHaveBeenCalledWith("/strategies");
  });

  it("passes false through unchanged", async () => {
    await toggleBotConfigEnabled("config-2", false);
    expect(setBotConfigEnabled).toHaveBeenCalledWith("config-2", false);
  });
});

describe("saveRiskParams", () => {
  it("parses the form fields into numbers and writes them, with the guard checkbox checked", async () => {
    const formData = new FormData();
    formData.set("maxPositionSize", "10");
    formData.set("dailyLossLimit", "5000");
    formData.set("dailyLossLimitEnabled", "on");

    await saveRiskParams("config-1", formData);

    expect(updateBotConfigRiskParams).toHaveBeenCalledWith("config-1", {
      maxPositionSize: 10,
      dailyLossLimit: 5000,
      dailyLossLimitEnabled: true,
    });
    expect(revalidatePath).toHaveBeenCalledWith("/strategies");
  });

  it("treats an absent (unchecked) checkbox as false", async () => {
    const formData = new FormData();
    formData.set("maxPositionSize", "10");
    formData.set("dailyLossLimit", "5000");
    // No dailyLossLimitEnabled field set -- matches an unchecked checkbox.

    await saveRiskParams("config-1", formData);

    expect(updateBotConfigRiskParams).toHaveBeenCalledWith(
      "config-1",
      expect.objectContaining({ dailyLossLimitEnabled: false })
    );
  });

  it("rejects non-numeric input without calling the DB", async () => {
    const formData = new FormData();
    formData.set("maxPositionSize", "not-a-number");
    formData.set("dailyLossLimit", "5000");

    await expect(saveRiskParams("config-1", formData)).rejects.toThrow();
    expect(updateBotConfigRiskParams).not.toHaveBeenCalled();
  });
});
