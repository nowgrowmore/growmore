import { describe, expect, it } from "vitest";
import { computePercentToSignal } from "./percent-to-signal";

describe("computePercentToSignal", () => {
  it("sma_crossover: solves the exact price where fast/slow SMAs cross", () => {
    // closes=[10,11,12], fast=2 slow=3 (matches sma_crossover.py's own hand
    // computed fixture): fast_sma=11.5, slow_sma=11.0, oldest_fast=11,
    // oldest_slow=10. Hand-solved crossing price is exactly 10 (verified: at
    // P=10, new_fast_sma = 11.5+(10-11)/2 = 11.0 = new_slow_sma).
    const result = computePercentToSignal(
      "sma_crossover",
      { fast_sma: 11.5, slow_sma: 11.0, oldest_fast: 11, oldest_slow: 10 },
      { fast_period: 2, slow_period: 3 },
      12
    );
    // fast(11.5) > slow(11.0) currently -> next cross is a SELL.
    expect(result.toBuyPct).toBeNull();
    expect(result.toSellPct).toBeCloseTo(((10 - 12) / 12) * 100, 2);
  });

  it("macd_trend: solves the exact price where MACD crosses its signal line", () => {
    // fast=2, slow=3, signal=2 -- state after CLOSES[:5]=[10,12,14,11,9]
    // (matches macd_trend.py's own hand-computed docstring): fast_ema=9.889,
    // slow_ema=10.25, macd=-0.361, signal=-0.046, current close (ltp)=9.
    // Hand-verified crossing price ~10.696 (new_macd == old signal there).
    const result = computePercentToSignal(
      "macd_trend",
      { fast_ema: 9.889, slow_ema: 10.25, macd: -0.361, signal: -0.046 },
      { fast_period: 2, slow_period: 3 },
      9
    );
    expect(result.toSellPct).toBeNull();
    expect(result.toBuyPct).toBeCloseTo(((10.696 - 9) / 9) * 100, 1);
  });

  it("rsi_mean_reversion: solves the price needed to cross back above oversold", () => {
    // period=3, oversold=30 -- state after CLOSES[:4]=[50,48,45,44] (matches
    // rsi_mean_reversion.py's own hand-computed docstring): avg_gain=0,
    // avg_loss=2.0, rsi=0, prev_close=45, current close (ltp)=44.
    // Hand-solved: boundary last_diff=2.142857 -> target price 47.142857.
    const result = computePercentToSignal(
      "rsi_mean_reversion",
      { rsi: 0, avg_gain: 0, avg_loss: 2.0, prev_close: 45 },
      { period: 3, oversold: 30, overbought: 70 },
      44
    );
    expect(result.toSellPct).toBeNull();
    expect(result.toBuyPct).toBeCloseTo(((47.142857 - 44) / 44) * 100, 2);
  });

  it("rsi_mean_reversion: solves the price needed to cross back below overbought", () => {
    // period=3, overbought=70 -- state after CLOSES[3:7]=[44,47,52,58]:
    // avg_gain=14/3, avg_loss=0, rsi=100, prev_close=52, ltp=58.
    // Hand-solved: boundary target price 48.5714 (verified: at that price,
    // new RSI = exactly 70).
    const result = computePercentToSignal(
      "rsi_mean_reversion",
      { rsi: 100, avg_gain: 14 / 3, avg_loss: 0, prev_close: 52 },
      { period: 3, oversold: 30, overbought: 70 },
      58
    );
    expect(result.toBuyPct).toBeNull();
    expect(result.toSellPct).toBeCloseTo(((48.5714 - 58) / 58) * 100, 1);
  });

  it("donchian_breakout: distance to the channel high/low", () => {
    const result = computePercentToSignal(
      "donchian_breakout",
      { channel_high: 110, channel_low: 90 },
      {},
      100
    );
    expect(result.toBuyPct).toBeCloseTo(10, 5);
    expect(result.toSellPct).toBeCloseTo(-10, 5);
  });

  it("bollinger_reversion: only the reachable side (already outside a band) gets a value", () => {
    const belowLower = computePercentToSignal(
      "bollinger_reversion",
      { upper_band: 110, lower_band: 90 },
      {},
      85
    );
    expect(belowLower.toBuyPct).toBeCloseTo(((90 - 85) / 85) * 100, 5);
    expect(belowLower.toSellPct).toBeNull();

    const inside = computePercentToSignal(
      "bollinger_reversion",
      { upper_band: 110, lower_band: 90 },
      {},
      100
    );
    expect(inside.toBuyPct).toBeNull();
    expect(inside.toSellPct).toBeNull();
  });

  it("vwap_session_bounce: distance to VWAP on the reachable side of a bullish-bias day", () => {
    const result = computePercentToSignal(
      "vwap_session_bounce",
      { cpr_bottom: 100, cpr_top: 110, vwap: 115 },
      {},
      112
    );
    expect(result.toSellPct).toBeNull();
    expect(result.toBuyPct).toBeCloseTo(((115 - 112) / 112) * 100, 5);
  });

  it("vwap_session_bounce: null when already on the far side of VWAP (needs a dip/rise first)", () => {
    const result = computePercentToSignal(
      "vwap_session_bounce",
      { cpr_bottom: 100, cpr_top: 110, vwap: 105 },
      {},
      115
    );
    expect(result.toBuyPct).toBeNull();
    expect(result.toSellPct).toBeNull();
  });

  it("returns nulls for an unrecognized strategy or missing data", () => {
    expect(computePercentToSignal("always_flip", {}, {}, 100)).toEqual({
      toBuyPct: null,
      toSellPct: null,
    });
    expect(computePercentToSignal("macd_trend", {}, {}, 100)).toEqual({
      toBuyPct: null,
      toSellPct: null,
    });
  });
});
