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
  regime_switch: {
    label: "ADX Regime-Switch",
    summary:
      "Routes between two other strategies based on trend strength (ADX): MACD Trend when the market is trending, a mean-reversion strategy (RSI or rolling-VWAP+EMA) when it's ranging. Backtested on real Gold Mini data and found to underperform the standalone strategies it's built from -- not enabled anywhere (see docs/goldmini-regime-switch-results.md).",
    params: {
      adx_period: { label: "ADX period", explain: "Bars used to compute trend-strength (ADX). 14 is the standard/original setting." },
      adx_trend_enter: { label: "Trend-enter threshold", explain: "ADX level above which the regime switches to \"trending\" (MACD takes over). 25 is the standard convention." },
      adx_trend_exit: { label: "Trend-exit threshold", explain: "ADX level below which the regime reverts to \"ranging\". Kept below the enter threshold (20 vs 25) deliberately, so ADX oscillating in between doesn't flip the regime back and forth." },
      ranging_strategy: { label: "Ranging-mode strategy", explain: "Which mean-reversion strategy runs during a ranging regime -- \"rsi\" or \"vwap_ema\"." },
    },
  },
  risk_managed: {
    label: "Risk-Managed (ATR stop)",
    summary:
      "Not a strategy of its own -- it wraps one of the others and adds the exits none of them have: a protective stop placed a fixed number of ATRs from entry, a Chandelier trailing stop that ratchets in your favour and never loosens, and an optional time stop. Measured across 13 same-strategy/same-instrument pairs it improved BOTH Sharpe and max drawdown in 8 and worsened both in 3 -- the gains are large where drawdown was worst (Gold Mini MACD: 19.1% -> 8.8%), the losses are on low-volatility contracts where a 2xATR stop sits inside normal noise. See docs/technical-debt.md.",
    params: {
      inner_strategy: { label: "Wrapped strategy", explain: "Which strategy generates the entry signals. The risk layer only decides when to get out." },
      inner_params: { label: "Wrapped strategy's params", explain: "Passed straight through to the wrapped strategy, unchanged." },
      atr_period: { label: "ATR period", explain: "Bars used to measure recent volatility (Average True Range), which sets how far away the stops sit. 14 is Wilder's standard." },
      initial_stop_atr: { label: "Initial stop (ATRs)", explain: "How many ATRs below the entry the protective stop starts. Lower = tighter, exits sooner, but risks being stopped out by ordinary noise." },
      trail_atr: { label: "Trailing stop (ATRs)", explain: "How many ATRs behind the best price seen the trailing stop follows. Only ever ratchets in your favour. Empty means no trailing stop -- the initial stop stays put." },
      max_bars_held: { label: "Time stop (bars)", explain: "Force an exit after this many bars regardless of price. Empty means no time limit, which is the default for these multi-day strategies." },
    },
  },
  ensemble_trend: {
    label: "Multi-Speed MACD Ensemble",
    summary:
      "Runs 5 MACD speeds at once (5/13/5 through 26/52/18) and goes long only when at least min_agreement of them are bullish -- there's no single lookback to select, so there's no selection-luck bias in choosing one. Almost always scores worse in isolation than the single luckiest MACD variant in a backtest -- that's the intended trade-off, giving up the lucky tail for a result that isn't just the winner of many tries. Wrapped in the risk-managed ATR stop layer, this combination is the only result in the full sweep that clears the conventional statistical-significance bar (DSR >= 0.95) -- see docs/backtest-results.md.",
    params: {
      min_agreement: { label: "Votes needed", explain: "How many of the 5 MACD members must agree (be bullish) for the ensemble to go long. Higher = more conservative, fewer but higher-conviction trades." },
    },
  },
  vwap_session_bounce: {
    label: "VWAP + CPR Session-Bounce",
    summary:
      "Intraday only, live data only -- can't be backtested (today's live session VWAP doesn't exist in historical bars). CPR (Central Pivot Range, from yesterday's high/low/close) sets the day's bullish/bearish bias; a live VWAP crossing in that direction is the entry trigger. A position is always flattened near the daily MCX close, since both CPR and VWAP reset every session. Validated by real paper trading instead of a backtest.",
    params: {},
  },
};

/** Params for an unrecognized/future strategy name fall back to a plain
 * key=value rendering elsewhere (formatStrategyParams) -- this just looks up
 * what we know how to explain. */
export function getStrategyInfo(strategyName: string): StrategyInfo | undefined {
  return STRATEGY_INFO[strategyName];
}
