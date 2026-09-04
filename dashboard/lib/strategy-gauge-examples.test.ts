import { describe, expect, it } from "vitest";
import { buildExampleGaugeConfig } from "./strategy-gauge-examples";
import { STRATEGY_INFO } from "./strategy-info";

describe("buildExampleGaugeConfig", () => {
  it("returns a gauge for every strategy in STRATEGY_INFO", () => {
    for (const name of Object.keys(STRATEGY_INFO)) {
      expect(buildExampleGaugeConfig(name)).not.toBeNull();
    }
  });

  it("returns null for an unrecognized strategy", () => {
    expect(buildExampleGaugeConfig("not_a_real_strategy")).toBeNull();
  });
});
