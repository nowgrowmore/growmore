/** Static reference data describing what each strategy family does and what
 * its parameters mean -- mirrors the real implementations in
 * bot/growmore_bot/strategies/. Kept as data (not fetched from the DB) since
 * it's about the strategy's logic, not any particular backtest run.
 */
export interface ParamInfo {
  label: string;
  explain: string;
}

export interface StrategyInfo {
  label: string;
  summary: string;
  params: Record<string, ParamInfo>;
}

export const STRATEGY_INFO: Record<string, StrategyInfo> = {
  sma_crossover: {
    label: "SMA Crossover",
    summary:
      "Trend-following: buys when the fast moving average crosses above the slow one, sells when it crosses back below.",
    params: {
      fast_period: { label: "Fast period", explain: "Bars averaged for the quick-reacting moving average. Smaller = reacts sooner, but more false signals." },
      slow_period: { label: "Slow period", explain: "Bars averaged for the baseline moving average it's compared against. Larger = smoother, slower to react." },
    },
  },
  donchian_breakout: {
    label: "Donchian Breakout",
    summary:
      "Breakout: buys when price closes above its highest close of the last N bars, exits when it closes below the lowest.",
    params: {
      period: { label: "Lookback period", explain: "Number of prior bars used to define the breakout channel (rolling high/low). Larger = only reacts to bigger, rarer breakouts." },
    },
  },
  rsi_mean_reversion: {
    label: "RSI Mean-Reversion",
    summary:
      "Mean-reversion: buys when RSI recovers back above the oversold line, sells when it drops back below the overbought line. Entering an extreme is never itself a signal -- only recovering from one is.",
    params: {
      period: { label: "RSI period", explain: "Bars used to compute average gains/losses for RSI. Smaller = more sensitive, more signals." },
      oversold: { label: "Oversold threshold", explain: "RSI level below which price is considered oversold. A buy fires when RSI climbs back above this line." },
      overbought: { label: "Overbought threshold", explain: "RSI level above which price is considered overbought. A sell fires when RSI drops back below this line." },
    },
  },
  macd_trend: {
    label: "MACD Trend",
    summary:
      "Momentum-based trend following: buys when the MACD line (fast EMA minus slow EMA) crosses above its own signal line, sells on the mirror cross below.",
    params: {
      fast_period: { label: "Fast EMA length", explain: "Bars for the quick-reacting exponential average feeding the MACD line." },
      slow_period: { label: "Slow EMA length", explain: "Bars for the baseline exponential average feeding the MACD line." },
      signal_period: { label: "Signal EMA length", explain: "Bars used to smooth the MACD line itself into the signal line it's compared against." },
    },
  },
  bollinger_reversion: {
    label: "Bollinger Band Reversion",
    summary:
      "Mean-reversion: buys when price closes back inside the lower band after closing outside it (a faded extreme), sells on the mirror condition at the upper band.",
    params: {
      period: { label: "Band period", explain: "Bars used for the moving average and standard deviation the bands are built from." },
      num_std: { label: "Band width (std devs)", explain: "How many standard deviations the bands sit from the average. Higher = wider bands, fewer but higher-conviction signals." },
    },
  },
};

/** Params for an unrecognized/future strategy name fall back to a plain
 * key=value rendering elsewhere (formatStrategyParams) -- this just looks up
 * what we know how to explain. */
export function getStrategyInfo(strategyName: string): StrategyInfo | undefined {
  return STRATEGY_INFO[strategyName];
}
