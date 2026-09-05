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

  it("donchian_breakout: says the breakout already fired instead of claiming price must still rise", () => {
    // Regression (independent code review 2026-09-04): both distances were
    // rendered through Math.abs, so a price already ABOVE the channel high
    // was described as still needing to "rise" that far to break above it.
    // Only the transition into a breakout signals (see the strategy's
    // prev_breakout_state), so there's no further BUY on that side at all.
    const above = explainSignal(
      "donchian_breakout",
      { channel_high: 110, channel_low: 90 },
      {},
      115
    );
    expect(above).toContain("already broken above");
    expect(above).not.toContain("needs to rise");
    expect(above).not.toContain("to BUY");

    const below = explainSignal(
      "donchian_breakout",
      { channel_high: 110, channel_low: 90 },
      {},
      85
    );
    expect(below).toContain("already broken below");
    expect(below).not.toContain("to SELL");
  });

  it("rsi_mean_reversion: RSI exactly on a threshold is in the extreme zone, not neutral", () => {
    // The strategy's own boundary is `prev <= oversold && rsi > oversold`,
    // so RSI sitting exactly ON the line is still inside the extreme zone.
    // This used to read "in neutral territory" at exactly 30/70.
    const atOversold = explainSignal(
      "rsi_mean_reversion",
      { rsi: 30, avg_gain: 0, avg_loss: 2, prev_close: 100 },
      { period: 14, oversold: 30, overbought: 70 },
      100
    );
    expect(atOversold).toContain("already below the oversold line");
    expect(atOversold).not.toContain("neutral territory");

    const atOverbought = explainSignal(
      "rsi_mean_reversion",
      { rsi: 70, avg_gain: 2, avg_loss: 0, prev_close: 100 },
      { period: 14, oversold: 30, overbought: 70 },
      100
    );
    expect(atOverbought).toContain("already above the overbought line");
    expect(atOverbought).not.toContain("neutral territory");
  });

  it("appends the exact percent move to the next signal when it's computable", () => {
    const text = explainSignal(
      "donchian_breakout",
      { channel_high: 110, channel_low: 90 },
      {},
      100
    );
    expect(text).toContain("+10.00% to BUY");
    expect(text).toContain("-10.00% to SELL");
  });

  it("omits the percent-move sentence entirely when it isn't computable", () => {
    const text = explainSignal("sma_crossover", {}, {}, 100);
    expect(text).not.toContain("to BUY");
    expect(text).not.toContain("to SELL");
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

  it("vwap_session_bounce: bullish bias, below vwap -- next BUY distance", () => {
    const text = explainSignal(
      "vwap_session_bounce",
      { cpr_bottom: 101.667, cpr_top: 105, vwap: 107 },
      {},
      106
    );
    expect(text).toMatch(/bullish bias/i);
    expect(text).toMatch(/BUY fires the moment/i);
  });

  it("vwap_session_bounce: bullish bias explicitly states SELL isn't possible and explains the real exit", () => {
    // Regression: a user asked "when does sell happen" after reading a
    // bullish-bias explanation that only described the BUY condition --
    // this strategy's SELL requires a bearish bias, so a position opened
    // on a bullish day has no signal-based exit; the real exit is the
    // automatic end-of-day flatten, which the text must say explicitly.
    const text = explainSignal(
      "vwap_session_bounce",
      { cpr_bottom: 101.667, cpr_top: 105, vwap: 155301 },
      {},
      155228
    );
    expect(text).toMatch(/SELL isn't possible while the bias stays bullish/i);
    expect(text).toMatch(/automatically.*closed near the end of the trading day/i);
  });

  it("vwap_session_bounce: bearish bias explicitly states BUY isn't possible and explains the real exit", () => {
    const text = explainSignal(
      "vwap_session_bounce",
      { cpr_bottom: 101.667, cpr_top: 105, vwap: 98 },
      {},
      99
    );
    expect(text).toMatch(/BUY isn't possible while the bias stays bearish/i);
    expect(text).toMatch(/automatically.*closed near the end of the trading day/i);
  });

  it("vwap_session_bounce: bullish bias, already above vwap -- needs a fresh dip first", () => {
    const text = explainSignal(
      "vwap_session_bounce",
      { cpr_bottom: 101.667, cpr_top: 105, vwap: 107 },
      {},
      108
    );
    expect(text).toMatch(/already above VWAP/i);
  });

  it("vwap_session_bounce: bearish bias, above vwap -- next SELL distance", () => {
    const text = explainSignal(
      "vwap_session_bounce",
      { cpr_bottom: 101.667, cpr_top: 105, vwap: 98 },
      {},
      99
    );
    expect(text).toMatch(/bearish bias/i);
    expect(text).toMatch(/SELL fires the moment/i);
  });

  it("vwap_session_bounce: price inside the CPR band -- no bias either way", () => {
    const text = explainSignal(
      "vwap_session_bounce",
      { cpr_bottom: 101.667, cpr_top: 105, vwap: 104 },
      {},
      103
    );
    expect(text).toMatch(/inside today's CPR band/i);
  });

  it("vwap_session_bounce: not enough data yet", () => {
    const text = explainSignal("vwap_session_bounce", {}, {}, 100);
    expect(text).toMatch(/not enough data/i);
  });

  it("regime_switch: trending regime explains MACD (the active sub-strategy)", () => {
    const text = explainSignal(
      "regime_switch",
      { adx: 32, regime: "trending", macd: 5, signal: 2, rsi: 40 },
      { ranging_strategy: "rsi", ranging_params: { oversold: 30, overbought: 70 } },
      100
    );
    expect(text).toMatch(/trending/i);
    expect(text).toContain("MACD Trend is in control");
    expect(text).toContain("SELL");
    expect(text).toContain("ignored while trending");
  });

  it("regime_switch: ranging regime with an rsi ranging sub-strategy", () => {
    const text = explainSignal(
      "regime_switch",
      { adx: 12, regime: "ranging", macd: 5, signal: 2, rsi: 25 },
      { ranging_strategy: "rsi", ranging_params: { oversold: 30, overbought: 70 } },
      100
    );
    expect(text).toMatch(/ranging/i);
    expect(text).toContain("RSI Mean-Reversion is in control");
    expect(text).toContain("BUY");
  });

  it("regime_switch: ranging regime with a vwap_ema ranging sub-strategy", () => {
    const text = explainSignal(
      "regime_switch",
      { adx: 12, regime: "ranging", macd: 5, signal: 2, vwap: 98, ema_fast: 101, ema_slow: 99 },
      { ranging_strategy: "vwap_ema", ranging_params: { vwap_period: 20, ema_fast: 8, ema_slow: 21 } },
      100
    );
    expect(text).toMatch(/ranging/i);
    expect(text).toContain("VWAP+EMA sub-strategy is in control");
    expect(text).not.toContain("[object Object]");
  });

  it("regime_switch: not enough data yet", () => {
    const text = explainSignal("regime_switch", {}, {}, 100);
    expect(text).toMatch(/not enough/i);
  });

  it("ensemble_trend: explains the vote count and whether it's enough to go long", () => {
    const long = explainSignal(
      "ensemble_trend",
      { bullish_votes: 3, votes_cast: 5, votes_needed: 3, members: 5 },
      { min_agreement: 3 },
      100
    );
    expect(long).toContain("3 of 5 MACD members");
    expect(long).toContain("ensemble is long");

    const flat = explainSignal(
      "ensemble_trend",
      { bullish_votes: 2, votes_cast: 5, votes_needed: 3, members: 5 },
      { min_agreement: 3 },
      100
    );
    expect(flat).toContain("stays flat");
  });

  it("ensemble_trend: not enough data yet", () => {
    const text = explainSignal("ensemble_trend", {}, {}, 100);
    expect(text).toMatch(/not enough/i);
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
