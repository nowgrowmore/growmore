import { describe, expect, it } from "vitest";
import {
  STRATEGIES_WITHOUT_GAUGES,
  buildExampleGaugeConfig,
} from "./strategy-gauge-examples";
import { STRATEGY_INFO } from "./strategy-info";

describe("buildExampleGaugeConfig", () => {
  it("returns a gauge for every strategy in STRATEGY_INFO that should have one", () => {
    for (const name of Object.keys(STRATEGY_INFO)) {
      if (STRATEGIES_WITHOUT_GAUGES.has(name)) continue;
      expect(buildExampleGaugeConfig(name), name).not.toBeNull();
    }
  });

  it("only exempts strategies that actually exist", () => {
    // Keeps the exemption list from silently outliving the strategy it was
    // written for, which would let a real omission slip through later.
    for (const name of STRATEGIES_WITHOUT_GAUGES) {
      expect(STRATEGY_INFO, name).toHaveProperty(name);
    }
  });

  it("returns null for an unrecognized strategy", () => {
    expect(buildExampleGaugeConfig("not_a_real_strategy")).toBeNull();
  });
});
