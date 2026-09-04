import { formatCurrency } from "./format";

/** Plain-language "why is it doing that" explanation for a strategy's
 * current HOLD/BUY/SELL status, built from its live indicator values
 * (bot_signal_state.indicators, written every tick by the bot -- see
 * Strategy.debug_state() in bot/growmore_bot/strategies/) plus its
 * configured params and the live quote price. Pure, DB-free, and covers
 * exactly the 5 real strategy families plus the always_flip demo -- an
 * unrecognized strategy name gets a generic fallback, never a crash.
 */

type Num = number | string | null | undefined;

function n(value: Num): number | null {
  if (value === null || value === undefined) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function explainSignal(
  strategyName: string,
  indicators: Record<string, Num> | null | undefined,
  params: Record<string, number | string> | null | undefined,
  ltp: Num
): string {
  const ind = indicators ?? {};
  const p = params ?? {};
  const price = n(ltp);

  switch (strategyName) {
    case "sma_crossover": {
      const fast = n(ind.fast_sma);
      const slow = n(ind.slow_sma);
      if (fast === null || slow === null) return "Not enough price history yet to compute both averages.";
      const gap = fast - slow;
      const direction = gap >= 0 ? "above" : "below";
      const nextSignal = gap >= 0 ? "SELL" : "BUY";
      const opposite = gap >= 0 ? "drops below" : "rises above";
      return (
        `The fast average (${formatCurrency(fast)}) is currently ${direction} the slow average ` +
        `(${formatCurrency(slow)}) by ${formatCurrency(Math.abs(gap))}. A ${nextSignal} fires the ` +
        `moment the fast average ${opposite} the slow one.`
      );
    }

    case "donchian_breakout": {
      const high = n(ind.channel_high);
      const low = n(ind.channel_low);
      if (high === null || low === null || price === null) {
        return "Not enough price history yet to compute the breakout channel.";
      }
      const toHigh = high - price;
      const toLow = price - low;
      return (
        `Price (${formatCurrency(price)}) needs to rise ${formatCurrency(Math.abs(toHigh))} to break ` +
        `above the recent high (${formatCurrency(high)}) and trigger a BUY, or fall ` +
        `${formatCurrency(Math.abs(toLow))} to break below the recent low (${formatCurrency(low)}) ` +
        `and trigger a SELL.`
      );
    }

    case "rsi_mean_reversion": {
      const rsi = n(ind.rsi);
      const oversold = n(p.oversold);
      const overbought = n(p.overbought);
      if (rsi === null) return "Not enough price history yet to compute RSI.";
      if (oversold !== null && rsi < oversold) {
        return (
          `RSI is ${rsi.toFixed(1)}, already below the oversold line (${oversold}). A BUY fires the ` +
          `moment it climbs back above ${oversold} -- entering oversold isn't itself a signal, only ` +
          `recovering from it is.`
        );
      }
      if (overbought !== null && rsi > overbought) {
        return (
          `RSI is ${rsi.toFixed(1)}, already above the overbought line (${overbought}). A SELL fires ` +
          `the moment it drops back below ${overbought} -- entering overbought isn't itself a signal, ` +
          `only recovering from it is.`
        );
      }
      const parts: string[] = [`RSI is ${rsi.toFixed(1)}, in neutral territory.`];
      if (oversold !== null) parts.push(`${(rsi - oversold).toFixed(1)} points above the oversold line (${oversold})`);
      if (overbought !== null) parts.push(`${(overbought - rsi).toFixed(1)} points below the overbought line (${overbought})`);
      return (
        parts[0] +
        (parts.length > 1 ? ` It's ${parts.slice(1).join(" and ")}.` : "") +
        " No signal until it first crosses one of those lines and then recovers back across it."
      );
    }

    case "macd_trend": {
      const macd = n(ind.macd);
      const signal = n(ind.signal);
      if (macd === null || signal === null) return "Not enough price history yet to compute MACD.";
      const gap = macd - signal;
      const direction = gap >= 0 ? "above" : "below";
      const nextSignal = gap >= 0 ? "SELL" : "BUY";
      const opposite = gap >= 0 ? "drops below" : "rises above";
      return (
        `The MACD line (${macd.toFixed(2)}) is currently ${direction} its signal line ` +
        `(${signal.toFixed(2)}) by ${Math.abs(gap).toFixed(2)}. A ${nextSignal} fires the moment MACD ` +
        `${opposite} the signal line.`
      );
    }

    case "bollinger_reversion": {
      const upper = n(ind.upper_band);
      const lower = n(ind.lower_band);
      if (upper === null || lower === null || price === null) {
        return "Not enough price history yet to compute the bands.";
      }
      if (price < lower) {
        return (
          `Price (${formatCurrency(price)}) has broken below the lower band (${formatCurrency(lower)}). ` +
          `A BUY fires the moment it closes back inside the bands -- breaking out isn't itself a ` +
          `signal, only recovering back in is.`
        );
      }
      if (price > upper) {
        return (
          `Price (${formatCurrency(price)}) has broken above the upper band (${formatCurrency(upper)}). ` +
          `A SELL fires the moment it closes back inside the bands.`
        );
      }
      return (
        `Price (${formatCurrency(price)}) is inside the bands (${formatCurrency(lower)} to ` +
        `${formatCurrency(upper)}). No signal until it first closes outside one of those bands and ` +
        `then recovers back inside.`
      );
    }

    case "vwap_session_bounce": {
      const vwap = n(ind.vwap);
      const cprBottom = n(ind.cpr_bottom);
      const cprTop = n(ind.cpr_top);
      if (price === null || vwap === null || cprBottom === null || cprTop === null) {
        return "Not enough data yet to compute today's VWAP and CPR levels.";
      }
      const aboveVwap = price > vwap;
      const vwapGap = Math.abs(price - vwap);
      // This strategy's SELL condition (price below CPR bottom, crossing
      // down through VWAP) can only ever fire on a bearish-bias day -- so
      // once a BUY opens a position on a bullish-bias day, there's no SELL
      // signal available to close it unless the bias flips entirely later
      // in the session. The real exit for the common case is automatic:
      // every position from this strategy is force-closed near the daily
      // MCX session close, since both CPR and VWAP reset every trading day.
      const exitNote =
        "Regardless of what happens above, any open position from this strategy is automatically " +
        "closed near the end of the trading day -- CPR and VWAP both reset every session, so a " +
        "position based on today's levels is never carried into tomorrow.";
      if (price > cprTop) {
        const nextStep = aboveVwap
          ? "already above VWAP -- a fresh BUY needs price to first dip back below VWAP, then cross back above it"
          : `a BUY fires the moment price rises back above VWAP (${formatCurrency(vwapGap)} away)`;
        return (
          `Price (${formatCurrency(price)}) is above today's CPR top (${formatCurrency(cprTop)}) -- ` +
          `a bullish bias day. It's currently ${aboveVwap ? "above" : "below"} the live session VWAP ` +
          `(${formatCurrency(vwap)}); ${nextStep}. A SELL isn't possible while the bias stays bullish -- ` +
          `it would need price to fall all the way below CPR bottom first. ${exitNote}`
        );
      }
      if (price < cprBottom) {
        const nextStep = !aboveVwap
          ? "already below VWAP -- a fresh SELL needs price to first rise back above VWAP, then cross back below it"
          : `a SELL fires the moment price falls back below VWAP (${formatCurrency(vwapGap)} away)`;
        return (
          `Price (${formatCurrency(price)}) is below today's CPR bottom (${formatCurrency(cprBottom)}) -- ` +
          `a bearish bias day. It's currently ${aboveVwap ? "above" : "below"} the live session VWAP ` +
          `(${formatCurrency(vwap)}); ${nextStep}. A BUY isn't possible while the bias stays bearish -- ` +
          `it would need price to rise all the way above CPR top first. ${exitNote}`
        );
      }
      return (
        `Price (${formatCurrency(price)}) is inside today's CPR band (${formatCurrency(cprBottom)} to ` +
        `${formatCurrency(cprTop)}) -- no trading bias either way today, so no signal regardless of ` +
        `where price sits versus VWAP (${formatCurrency(vwap)}) until it moves outside the CPR band. ${exitNote}`
      );
    }

    case "always_flip":
      return "Demo/test strategy, not a real trading signal -- it deliberately alternates BUY and SELL every single tick to prove the pipeline works end to end.";

    default:
      return "No explanation available for this strategy yet.";
  }
}
