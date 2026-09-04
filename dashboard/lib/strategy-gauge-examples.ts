import { buildGaugeConfig } from "./strategy-gauge";
import type { LevelGaugeProps } from "@/components/LevelGauge";
import type { BotConfig } from "./types";

/** Illustrative, made-up (never real) example scenario per strategy family,
 * for the layman-facing Strategy Guide page -- built through the SAME
 * `buildGaugeConfig` real configs use, so the "how it generally works"
 * diagram and the "here's the real one right now" diagram are guaranteed
 * to render identically, not two parallel implementations that could drift.
 */
const EXAMPLE_SCENARIOS: Record<
  string,
  { strategy_params: Record<string, number | string>; signal_indicators: Record<string, number>; signal_ltp?: string }
> = {
  rsi_mean_reversion: {
    strategy_params: { period: 7, oversold: 30, overbought: 70 },
    signal_indicators: { rsi: 26.9 },
  },
  macd_trend: {
    strategy_params: { fast_period: 12, slow_period: 26, signal_period: 9 },
    signal_indicators: { macd: -0.14, signal: 0.05 },
  },
  sma_crossover: {
    strategy_params: { fast_period: 5, slow_period: 20 },
    signal_indicators: { fast_sma: 101, slow_sma: 98 },
  },
  donchian_breakout: {
    strategy_params: { period: 20 },
    signal_indicators: { channel_high: 106, channel_low: 94 },
    signal_ltp: "100",
  },
  bollinger_reversion: {
    strategy_params: { period: 20, num_std: 2.0 },
    signal_indicators: { upper_band: 110, lower_band: 90 },
    signal_ltp: "95",
  },
  regime_switch: {
    strategy_params: { adx_period: 14, adx_trend_enter: 25, adx_trend_exit: 20, ranging_strategy: "rsi" },
    signal_indicators: { adx: 32, regime: 1 }, // regime is a string in real data; unused by the gauge
  },
  vwap_session_bounce: {
    strategy_params: {},
    signal_indicators: { cpr_bottom: 101.67, cpr_pivot: 103.33, cpr_top: 105, vwap: 104 },
    signal_ltp: "106",
  },
};

export function buildExampleGaugeConfig(strategyName: string): LevelGaugeProps | null {
  const scenario = EXAMPLE_SCENARIOS[strategyName];
  if (!scenario) return null;
  const fakeConfig = {
    id: "example",
    strategy_id: "example",
    instrument_id: "example",
    enabled: true,
    virtual_capital: "0",
    max_position_size: "0",
    daily_loss_limit: "0",
    mode: "paper",
    updated_at: "",
    strategy_name: strategyName,
    ...scenario,
  } as unknown as BotConfig;
  return buildGaugeConfig(fakeConfig);
}
