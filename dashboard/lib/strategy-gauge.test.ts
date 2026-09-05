import { describe, expect, it } from "vitest";
import { buildGaugeConfig } from "./strategy-gauge";
import type { BotConfig } from "./types";

function makeConfig(overrides: Partial<BotConfig>): BotConfig {
  return {
    id: "c1",
    strategy_id: "s1",
    instrument_id: "i1",
    enabled: true,
    virtual_capital: "250000",
    max_position_size: "1",
    daily_loss_limit: "15000",
    daily_loss_limit_enabled: true,
    mode: "paper",
    updated_at: "2026-01-01T00:00:00Z",
    strategy_name: "rsi_mean_reversion",
    strategy_params: { period: 7, oversold: 30, overbought: 70 },
    signal_ltp: "155000",
    signal_indicators: {},
    ...overrides,
  };
}

describe("buildGaugeConfig", () => {
  it("returns null when there are no indicators yet", () => {
    const config = makeConfig({ signal_indicators: null });
    expect(buildGaugeConfig(config)).toBeNull();
  });

  it("returns null for an unrecognized strategy", () => {
    const config = makeConfig({ strategy_name: "some_future_strategy", signal_indicators: { x: 1 } });
    expect(buildGaugeConfig(config)).toBeNull();
  });

  describe("rsi_mean_reversion", () => {
    it("builds a 0-100 gauge with oversold/overbought reference lines", () => {
      const config = makeConfig({
        strategy_name: "rsi_mean_reversion",
        strategy_params: { period: 7, oversold: 30, overbought: 70 },
        signal_indicators: { rsi: 26.94 },
      });
      const gauge = buildGaugeConfig(config)!;
      expect(gauge.min).toBe(0);
      expect(gauge.max).toBe(100);
      expect(gauge.markers).toEqual([{ value: 26.94, label: "RSI", color: expect.any(String) }]);
      expect(gauge.referenceLines).toEqual([
        { value: 30, label: "Oversold" },
        { value: 70, label: "Overbought" },
      ]);
    });
  });

  describe("macd_trend", () => {
    it("builds a symmetric-around-zero gauge with macd and signal markers", () => {
      const config = makeConfig({
        strategy_name: "macd_trend",
        strategy_params: { fast_period: 12, slow_period: 26, signal_period: 9 },
        signal_indicators: { macd: -0.14, signal: 0.05 },
      });
      const gauge = buildGaugeConfig(config)!;
      expect(gauge.min).toBeLessThan(0);
      expect(gauge.max).toBeGreaterThan(0);
      expect(gauge.markers.map((m) => m.label)).toEqual(["MACD", "Signal"]);
      expect(gauge.referenceLines).toEqual([{ value: 0, label: "Zero line" }]);
    });
  });

  describe("donchian_breakout", () => {
    it("builds a channel gauge with the current price marker", () => {
      const config = makeConfig({
        strategy_name: "donchian_breakout",
        strategy_params: { period: 20 },
        signal_indicators: { channel_high: 156000, channel_low: 152000 },
        signal_ltp: "154000",
      });
      const gauge = buildGaugeConfig(config)!;
      expect(gauge.min).toBeLessThanOrEqual(152000);
      expect(gauge.max).toBeGreaterThanOrEqual(156000);
      expect(gauge.markers).toEqual([{ value: 154000, label: "Price", color: expect.any(String) }]);
      expect(gauge.referenceLines).toEqual([
        { value: 152000, label: "Channel low" },
        { value: 156000, label: "Channel high" },
      ]);
    });
  });

  describe("bollinger_reversion", () => {
    it("builds a band gauge with the current price marker", () => {
      const config = makeConfig({
        strategy_name: "bollinger_reversion",
        strategy_params: { period: 20, num_std: 2.0 },
        signal_indicators: { upper_band: 110, lower_band: 95 },
        signal_ltp: "100",
      });
      const gauge = buildGaugeConfig(config)!;
      expect(gauge.referenceLines).toEqual([
        { value: 95, label: "Lower band" },
        { value: 110, label: "Upper band" },
      ]);
    });
  });

  describe("regime_switch", () => {
    it("builds a 0-100 ADX gauge with enter/exit reference lines", () => {
      const config = makeConfig({
        strategy_name: "regime_switch",
        strategy_params: {
          ranging_strategy: "rsi",
          adx_period: 14,
          adx_trend_enter: 25,
          adx_trend_exit: 20,
        },
        signal_indicators: { adx: 32.5, regime: "trending" },
      });
      const gauge = buildGaugeConfig(config)!;
      expect(gauge.min).toBe(0);
      expect(gauge.max).toBe(100);
      expect(gauge.markers).toEqual([{ value: 32.5, label: "ADX", color: expect.any(String) }]);
      expect(gauge.referenceLines).toEqual([
        { value: 20, label: "Exit trend" },
        { value: 25, label: "Enter trend" },
      ]);
    });
  });

  describe("vwap_session_bounce", () => {
    it("builds a price gauge with CPR bands, VWAP, and current price", () => {
      const config = makeConfig({
        strategy_name: "vwap_session_bounce",
        strategy_params: {},
        signal_indicators: { cpr_bottom: 151440, cpr_pivot: 151826, cpr_top: 152212, vwap: 155302 },
        signal_ltp: "155119",
      });
      const gauge = buildGaugeConfig(config)!;
      expect(gauge.referenceLines?.map((r) => r.label)).toEqual([
        "CPR bottom",
        "CPR pivot",
        "CPR top",
        "VWAP",
      ]);
      expect(gauge.markers).toEqual([{ value: 155119, label: "Price", color: expect.any(String) }]);
    });

    it("returns null when CPR hasn't been computed yet", () => {
      const config = makeConfig({
        strategy_name: "vwap_session_bounce",
        signal_indicators: { vwap: 155302 },
      });
      expect(buildGaugeConfig(config)).toBeNull();
    });
  });
});
