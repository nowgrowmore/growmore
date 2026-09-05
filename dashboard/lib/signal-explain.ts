import { formatCurrency } from "./format";
import { computePercentToSignal } from "./percent-to-signal";

/** Plain-language "why is it doing that" explanation for a strategy's
 * current HOLD/BUY/SELL status, built from its live indicator values
 * (bot_signal_state.indicators, written every tick by the bot -- see
 * Strategy.debug_state() in bot/growmore_bot/strategies/) plus its
 * configured params and the live quote price. Pure, DB-free, and covers
 * exactly the 5 real strategy families plus the always_flip demo -- an
 * unrecognized strategy name gets a generic fallback, never a crash.
 */

type Num = number | string | null | undefined;

function n(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const parsed = Number(value as number | string);
  return Number.isFinite(parsed) ? parsed : null;
}

export function explainSignal(
  strategyName: string,
  indicators: Record<string, Num> | null | undefined,
  params: Record<string, unknown> | null | undefined,
  ltp: Num
): string {
  const base = explainSignalBase(strategyName, indicators, params, ltp);
  const { toBuyPct, toSellPct } = computePercentToSignal(strategyName, indicators, params, ltp);
  const moveNotes: string[] = [];
  if (toBuyPct !== null) moveNotes.push(`${toBuyPct >= 0 ? "+" : ""}${toBuyPct.toFixed(2)}% to BUY`);
  if (toSellPct !== null) moveNotes.push(`${toSellPct >= 0 ? "+" : ""}${toSellPct.toFixed(2)}% to SELL`);
  if (moveNotes.length === 0) return base;
  return `${base} That's ${moveNotes.join(" / ")} from here.`;
}

function explainSignalBase(
  strategyName: string,
  indicators: Record<string, Num> | null | undefined,
  params: Record<string, unknown> | null | undefined,
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
      // Only the TRANSITION into a breakout signals (see
      // donchian_breakout.py's prev_breakout_state), so once price is
      // already outside the channel there's no further signal on that side
      // until it comes back inside first. The old wording used Math.abs on
      // both distances and so claimed price still "needs to rise" to break
      // a high it had already broken.
      if (price > high) {
        return (
          `Price (${formatCurrency(price)}) has already broken above the recent high ` +
          `(${formatCurrency(high)}) -- that BUY has fired. No further BUY until price falls back ` +
          `inside the channel and breaks out again; a SELL needs a fall of ` +
          `${formatCurrency(price - low)} to below the recent low (${formatCurrency(low)}).`
        );
      }
      if (price < low) {
        return (
          `Price (${formatCurrency(price)}) has already broken below the recent low ` +
          `(${formatCurrency(low)}) -- that SELL has fired. No further SELL until price rises back ` +
          `inside the channel and breaks out again; a BUY needs a rise of ` +
          `${formatCurrency(high - price)} to above the recent high (${formatCurrency(high)}).`
        );
      }
      return (
        `Price (${formatCurrency(price)}) needs to rise ${formatCurrency(high - price)} to break ` +
        `above the recent high (${formatCurrency(high)}) and trigger a BUY, or fall ` +
        `${formatCurrency(price - low)} to break below the recent low (${formatCurrency(low)}) ` +
        `and trigger a SELL.`
      );
    }

    case "rsi_mean_reversion": {
      const rsi = n(ind.rsi);
      const oversold = n(p.oversold);
      const overbought = n(p.overbought);
      if (rsi === null) return "Not enough price history yet to compute RSI.";
      // `<=` / `>=`, matching the strategy's own boundary: a BUY fires when
      // RSI was AT OR below oversold and then climbs past it (see
      // rsi_mean_reversion.py's `prev <= self.oversold and rsi > ...`), so
      // RSI sitting exactly ON the line is in the extreme zone, not neutral
      // -- and percent-to-signal.ts already used `<=` for the same reason.
      if (oversold !== null && rsi <= oversold) {
        return (
          `RSI is ${rsi.toFixed(1)}, already below the oversold line (${oversold}). A BUY fires the ` +
          `moment it climbs back above ${oversold} -- entering oversold isn't itself a signal, only ` +
          `recovering from it is.`
        );
      }
      if (overbought !== null && rsi >= overbought) {
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

    case "regime_switch": {
      const adx = n(ind.adx);
      const macd = n(ind.macd);
      const signal = n(ind.signal);
      const regime = ind.regime !== undefined && ind.regime !== null ? String(ind.regime) : null;
      const rangingParams = (p.ranging_params ?? {}) as Record<string, unknown>;
      const rangingStrategy = typeof p.ranging_strategy === "string" ? p.ranging_strategy : null;
      const adxEnter = n(p.adx_trend_enter) ?? 25;
      const adxExit = n(p.adx_trend_exit) ?? 20;
      if (adx === null || regime === null) {
        return "Not enough price history yet to compute ADX for the regime switch.";
      }
      const trendNote =
        `ADX is ${adx.toFixed(1)} (enters TRENDING above ${adxEnter}, reverts to RANGING below ` +
        `${adxExit} -- a dead zone in between stops it flip-flopping on every small wobble).`;

      if (regime === "trending") {
        if (macd === null || signal === null) {
          return `${trendNote} Currently TRENDING, but not enough history yet to compute MACD.`;
        }
        const gap = macd - signal;
        const direction = gap >= 0 ? "above" : "below";
        const nextSignal = gap >= 0 ? "SELL" : "BUY";
        const opposite = gap >= 0 ? "drops below" : "rises above";
        return (
          `${trendNote} Currently TRENDING, so MACD Trend is in control: its line (${macd.toFixed(2)}) is ` +
          `${direction} the signal line (${signal.toFixed(2)}) by ${Math.abs(gap).toFixed(2)}. A ${nextSignal} ` +
          `fires the moment MACD ${opposite} the signal line. The ranging-mode sub-strategy keeps computing in ` +
          `the background so it stays warm, but its signal is ignored while trending.`
        );
      }

      // Ranging: the active sub-strategy is whichever this config's
      // ranging_strategy param names -- "rsi" or "vwap_ema".
      if (rangingStrategy === "rsi") {
        const rsi = n(ind.rsi);
        const oversold = n(rangingParams.oversold) ?? 30;
        const overbought = n(rangingParams.overbought) ?? 70;
        if (rsi === null) {
          return `${trendNote} Currently RANGING, but not enough history yet to compute the RSI sub-strategy.`;
        }
        if (rsi <= oversold) {
          return (
            `${trendNote} Currently RANGING, so RSI Mean-Reversion is in control: RSI is ${rsi.toFixed(1)}, ` +
            `already at/below the oversold line (${oversold}). A BUY fires the moment it climbs back above ` +
            `${oversold}. MACD Trend keeps computing in the background so it stays warm, but is ignored while ranging.`
          );
        }
        if (rsi >= overbought) {
          return (
            `${trendNote} Currently RANGING, so RSI Mean-Reversion is in control: RSI is ${rsi.toFixed(1)}, ` +
            `already at/above the overbought line (${overbought}). A SELL fires the moment it drops back below ` +
            `${overbought}. MACD Trend keeps computing in the background so it stays warm, but is ignored while ranging.`
          );
        }
        return (
          `${trendNote} Currently RANGING, so RSI Mean-Reversion is in control: RSI is ${rsi.toFixed(1)}, in ` +
          `neutral territory (between ${oversold} and ${overbought}). No signal until it first crosses one of ` +
          `those lines and then recovers back across it.`
        );
      }

      // vwap_ema ranging mode.
      const vwap = n(ind.vwap);
      const emaFast = n(ind.ema_fast);
      const emaSlow = n(ind.ema_slow);
      if (vwap === null || emaFast === null || emaSlow === null || price === null) {
        return `${trendNote} Currently RANGING, but not enough history yet to compute the VWAP+EMA sub-strategy.`;
      }
      const aboveVwap = price > vwap;
      const emaBullish = emaFast >= emaSlow;
      return (
        `${trendNote} Currently RANGING, so the VWAP+EMA sub-strategy is in control: price (${formatCurrency(price)}) ` +
        `is ${aboveVwap ? "above" : "below"} its rolling VWAP (${formatCurrency(vwap)}), and the fast EMA ` +
        `(${emaFast.toFixed(2)}) is ${emaBullish ? "above" : "below"} the slow EMA (${emaSlow.toFixed(2)}). A BUY ` +
        `needs price to cross from below to above VWAP while the fast EMA is at/above the slow one; a SELL needs ` +
        `the mirror image below. MACD Trend keeps computing in the background so it stays warm, but is ignored ` +
        `while ranging.`
      );
    }

    case "ensemble_trend": {
      const bullishVotes = n(ind.bullish_votes);
      const votesCast = n(ind.votes_cast);
      const votesNeeded = n(ind.votes_needed);
      const members = n(ind.members);
      if (bullishVotes === null || votesCast === null || votesNeeded === null || members === null) {
        return "Not enough price history yet for the 5 MACD members to all have an opinion.";
      }
      const isLong = bullishVotes >= votesNeeded;
      return (
        `${bullishVotes} of ${votesCast} MACD members (of ${members} total) are currently bullish -- ` +
        `${votesNeeded} needed to go long. ${isLong ? "That's enough: the ensemble is long." : "Not enough yet: the ensemble stays flat."} ` +
        `Every member keeps updating every tick regardless of its own vote, so the count can shift ` +
        `up or down as individual MACD crossings happen, without necessarily crossing the ` +
        `${votesNeeded}-vote threshold.`
      );
    }

    case "risk_managed": {
      const atr = n(ind.atr);
      if (atr === null || price === null) {
        return "Not enough price history yet to measure volatility (ATR), so no stop can be placed.";
      }
      const stopMultiple = n(p.initial_stop_atr) ?? 2;
      const trail = n(p.trail_atr);
      const stop = price - stopMultiple * atr;
      const distancePct = ((price - stop) / price) * 100;
      const inner = typeof p.inner_strategy === "string" ? p.inner_strategy : "the wrapped strategy";
      return (
        `Entries come from ${inner}; this layer only decides when to get out. Recent volatility ` +
        `(ATR) is ${formatCurrency(atr)}, so a fresh ${stopMultiple}x-ATR stop would sit around ` +
        `${formatCurrency(stop)} — about ${distancePct.toFixed(1)}% below the current price ` +
        `(${formatCurrency(price)}).` +
        (trail !== null
          ? ` Once in profit a ${trail}x-ATR trailing stop follows the best price seen and never loosens.`
          : " There is no trailing stop configured, so the initial stop stays where it was placed.") +
        " Note the stop is checked once per 5-minute poll, not resting at the exchange, so a fast" +
        " move can fill worse than the level shown."
      );
    }

    case "vol_filtered": {
      const vol = n(ind.realized_vol);
      const threshold = n(ind.vol_threshold);
      const cap = n(p.percentile_cap) ?? 0.9;
      const inner = typeof p.inner_strategy === "string" ? p.inner_strategy : "the wrapped strategy";
      const pctile = Math.round(cap * 100);
      if (vol === null) {
        return (
          `Entries come from ${inner}. This layer also blocks new entries when the market is ` +
          `unusually volatile, but there is not enough price history yet to measure that.`
        );
      }
      if (threshold === null) {
        return (
          `Entries come from ${inner}. Realised volatility is ${(vol * 100).toFixed(1)}% ` +
          `annualised, but there is not yet enough history to rank it against, so no entry is ` +
          `being blocked.`
        );
      }
      const blocked = vol > threshold;
      return (
        `Entries come from ${inner}; this layer only decides whether a new one is allowed. ` +
        `Realised volatility is ${(vol * 100).toFixed(1)}% annualised against a ${pctile}th-percentile ` +
        `threshold of ${(threshold * 100).toFixed(1)}% from its own trailing history. ` +
        (blocked
          ? "That is above the threshold, so no NEW position will be opened until it calms down. " +
            "An existing position is unaffected -- exits are never blocked."
          : "That is below the threshold, so entries are currently allowed.") +
        " The threshold is the instrument's own history rather than a fixed number, which is what" +
        " lets one setting work on both gold and silver despite their very different volatility."
      );
    }

    case "buy_and_hold":
      return (
        "Holds one lot, always. It buys whenever it finds itself flat, which is what keeps it " +
        "invested across contract rollovers -- MCX futures expire, so the scheduler force-closes " +
        "before the delivery window and this re-enters on the new contract month. There is no " +
        "signal to wait for and no stop: it exists as the benchmark every other strategy is " +
        "measured against, and out-of-sample it beats the trading system on five of eight contracts."
      );

    case "always_flip":
      return "Demo/test strategy, not a real trading signal -- it deliberately alternates BUY and SELL every single tick to prove the pipeline works end to end.";

    default:
      return "No explanation available for this strategy yet.";
  }
}
