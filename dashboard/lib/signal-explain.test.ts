import { describe, expect, it } from "vitest";
import { explainSignal } from "./signal-explain";

describe("explainSignal", () => {
  it("sma_crossover: explains the gap and which signal fires next when fast is above", () => {
    const text = explainSignal("sma_crossover", { fast_sma: 105, slow_sma: 100 }, {}, 106);
    expect(text).toContain("above");
    expect(text).toContain("SELL");
  });

  it("sma_crossover: explains a BUY is next when fast is below", () => {
    const text = explainSignal("sma_crossover", { fast_sma: 95, slow_sma: 100 }, {}, 96);
    expect(text).toContain("below");
    expect(text).toContain("BUY");
  });

  it("sma_crossover: handles missing indicators gracefully", () => {
    const text = explainSignal("sma_crossover", {}, {}, 100);
    expect(text).toMatch(/not enough/i);
  });

  it("donchian_breakout: explains distance to both channel edges", () => {
    const text = explainSignal(
      "donchian_breakout",
      { channel_high: 110, channel_low: 90 },
      {},
      100
    );
    expect(text).toContain("BUY");
    expect(text).toContain("SELL");
  });

  it("rsi_mean_reversion: already oversold, waiting to recover above the line", () => {
    const text = explainSignal(
      "rsi_mean_reversion",
      { rsi: 25 },
      { oversold: 30, overbought: 70 },
      100
    );
    expect(text).toContain("BUY");
    expect(text).toMatch(/oversold/i);
  });

  it("rsi_mean_reversion: already overbought, waiting to recover below the line", () => {
    const text = explainSignal(
      "rsi_mean_reversion",
      { rsi: 78 },
      { oversold: 30, overbought: 70 },
      100
    );
    expect(text).toContain("SELL");
    expect(text).toMatch(/overbought/i);
  });

  it("rsi_mean_reversion: neutral zone mentions distance to both thresholds", () => {
    const text = explainSignal(
      "rsi_mean_reversion",
      { rsi: 50 },
      { oversold: 30, overbought: 70 },
      100
    );
    expect(text).toContain("20.0 points above the oversold line (30)");
    expect(text).toContain("20.0 points below the overbought line (70)");
  });

  it("macd_trend: explains the gap and which signal fires next", () => {
    const text = explainSignal("macd_trend", { macd: -5, signal: 2 }, {}, 100);
    expect(text).toContain("below");
    expect(text).toContain("BUY");
  });

  it("bollinger_reversion: price broken below the lower band", () => {
    const text = explainSignal(
      "bollinger_reversion",
      { upper_band: 110, lower_band: 95 },
      {},
      90
    );
    expect(text).toContain("BUY");
    expect(text).toMatch(/broken below/i);
  });

  it("bollinger_reversion: price broken above the upper band", () => {
    const text = explainSignal(
      "bollinger_reversion",
      { upper_band: 110, lower_band: 95 },
      {},
      115
    );
    expect(text).toContain("SELL");
    expect(text).toMatch(/broken above/i);
  });

  it("bollinger_reversion: price inside the bands", () => {
    const text = explainSignal(
      "bollinger_reversion",
      { upper_band: 110, lower_band: 95 },
      {},
      100
    );
    expect(text).toMatch(/inside the bands/i);
  });

  it("always_flip: explains it's a demo strategy", () => {
    const text = explainSignal("always_flip", { last_close: 100 }, {}, 100);
    expect(text).toMatch(/demo|test/i);
  });

  it("unknown strategy: generic fallback, never throws", () => {
    expect(() => explainSignal("some_future_strategy", {}, {}, 100)).not.toThrow();
    expect(explainSignal("some_future_strategy", {}, {}, 100)).toMatch(/no explanation/i);
  });

  it("handles null/undefined indicators and params without throwing", () => {
    expect(() => explainSignal("macd_trend", null, null, null)).not.toThrow();
  });
});
