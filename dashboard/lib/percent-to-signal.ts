/** "How far away is the next signal?" as a percentage price move, computed
 * per strategy family from the same live indicators signal-explain.ts
 * already reads (bot_signal_state.indicators) plus strategy_params and the
 * live LTP. Pure, DB-free.
 *
 * Every result is `(targetPrice - ltp) / ltp * 100`, signed: positive means
 * price needs to RISE that much, negative means it needs to FALL. `null`
 * means "not applicable right now" (e.g. a SELL that can't fire while the
 * current bias is bullish), matching the same gating logic signal-explain.ts
 * already describes in prose for that case.
 *
 * For MACD/RSI/SMA crossover (indicator-derived, not a direct price level),
 * the target price is solved algebraically by holding every other input
 * fixed and asking "what price would move this indicator to its threshold" --
 * see bot/growmore_bot/strategies/{macd_trend,rsi_mean_reversion,
 * sma_crossover}.py's debug_state() for the raw internals this depends on.
 *
 * The model, and it matters: the bot's scheduler rebuilds a fresh strategy
 * on EVERY tick and warms it up from daily history ending YESTERDAY, then
 * feeds it exactly one live price (see scheduler/run.py's `_warm_up_strategy`).
 * So the next tick sees this very same window/EMA state with only the live
 * price REPLACED -- never a bar longer. Every solver here therefore holds
 * the whole pre-live-price state fixed and moves only the live price, which
 * makes these exact answers for the next tick, not approximations. Solving
 * instead for a price APPENDED as a new bar (rolling values out of the
 * window, or taking one more EMA step from the current EMA) models a day
 * that never happens -- the bug fixed here on 2026-09-04, which understated
 * the real MACD distance by more than 3x.
 */

type Num = number | string | null | undefined;

function n(value: Num): number | null {
  if (value === null || value === undefined) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function pctMove(target: number | null, ltp: number | null): number | null {
  if (target === null || ltp === null || ltp === 0) return null;
  return ((target - ltp) / ltp) * 100;
}

export interface PercentToSignal {
  toBuyPct: number | null;
  toSellPct: number | null;
}

const NONE: PercentToSignal = { toBuyPct: null, toSellPct: null };

export function computePercentToSignal(
  strategyName: string,
  indicators: Record<string, Num> | null | undefined,
  params: Record<string, number | string> | null | undefined,
  ltp: Num
): PercentToSignal {
  const ind = indicators ?? {};
  const p = params ?? {};
  const price = n(ltp);
  if (price === null) return NONE;

  switch (strategyName) {
    case "sma_crossover": {
      const fastSma = n(ind.fast_sma);
      const slowSma = n(ind.slow_sma);
      const fastPeriod = n(p.fast_period);
      const slowPeriod = n(p.slow_period);
      if (
        fastSma === null || slowSma === null ||
        fastPeriod === null || slowPeriod === null || fastPeriod === slowPeriod ||
        fastPeriod === 0 || slowPeriod === 0
      ) {
        return NONE;
      }
      // Replacing the live price P in the newest slot moves each average by
      // (P - ltp)/period, so the two are equal when
      //   (P - ltp) * (1/fast - 1/slow) = slowSma - fastSma.
      const denom = 1 / fastPeriod - 1 / slowPeriod;
      const target = price + (slowSma - fastSma) / denom;
      const move = pctMove(target, price);
      // fast currently above slow -> next cross is a SELL (fast falling back
      // below slow); fast below slow -> next cross is a BUY.
      return fastSma >= slowSma ? { toBuyPct: null, toSellPct: move } : { toBuyPct: move, toSellPct: null };
    }

    case "macd_trend": {
      const fastEma = n(ind.fast_ema);
      const slowEma = n(ind.slow_ema);
      const macd = n(ind.macd);
      const signal = n(ind.signal);
      const fastPeriod = n(p.fast_period);
      const slowPeriod = n(p.slow_period);
      const signalPeriod = n(p.signal_period);
      if (
        fastEma === null || slowEma === null || macd === null || signal === null ||
        fastPeriod === null || slowPeriod === null || signalPeriod === null
      ) {
        return NONE;
      }
      const kFast = 2 / (fastPeriod + 1);
      const kSlow = 2 / (slowPeriod + 1);
      const kSignal = 2 / (signalPeriod + 1);
      const denom = kFast - kSlow;
      if (denom === 0 || kSignal >= 1) return NONE;
      // Recover the pre-live-price (yesterday's) signal line, which is the
      // base the next tick's own single EMA step starts from:
      //   signal = macd*kSignal + signalPrev*(1-kSignal).
      // Substituting the one-step EMAs for a hypothetical price P and
      // solving macd(P) == signal(P) collapses -- every EMA moves by the
      // same k-weighted amount -- to macd(P) == signalPrev, i.e.
      //   P = ltp + (signalPrev - macd) / (kFast - kSlow).
      const signalPrev = (signal - macd * kSignal) / (1 - kSignal);
      const target = price + (signalPrev - macd) / denom;
      const move = pctMove(target, price);
      // macd currently above signal -> next cross is a SELL; below -> BUY.
      return macd >= signal ? { toBuyPct: null, toSellPct: move } : { toBuyPct: move, toSellPct: null };
    }

    case "rsi_mean_reversion": {
      const avgGain = n(ind.avg_gain);
      const avgLoss = n(ind.avg_loss);
      const prevClose = n(ind.prev_close);
      const rsi = n(ind.rsi);
      const oversold = n(p.oversold) ?? 30;
      const overbought = n(p.overbought) ?? 70;
      const period = n(p.period);
      if (
        avgGain === null || avgLoss === null || prevClose === null || rsi === null || period === null
      ) {
        return NONE;
      }
      const lastDiff = price - prevClose;
      const gainSumExLast = avgGain * period - Math.max(0, lastDiff);
      const lossSumExLast = avgLoss * period - Math.max(0, -lastDiff);

      let toBuyPct: number | null = null;
      let toSellPct: number | null = null;

      // Only meaningful when currently at/below oversold -- the strategy
      // only signals BUY on recovering back above it (see
      // rsi_mean_reversion.py's crossing logic).
      if (rsi <= oversold && lossSumExLast > 0) {
        const targetLastDiff = (lossSumExLast * oversold) / (100 - oversold) - gainSumExLast;
        if (targetLastDiff > 0) {
          toBuyPct = pctMove(prevClose + targetLastDiff, price);
        }
      }
      // Only meaningful when currently at/above overbought -- SELL only
      // fires on recovering back below it.
      if (rsi >= overbought && oversold !== undefined && gainSumExLast > 0) {
        const targetLastDiff = lossSumExLast - (gainSumExLast * (100 - overbought)) / overbought;
        if (targetLastDiff < 0) {
          toSellPct = pctMove(prevClose + targetLastDiff, price);
        }
      }
      return { toBuyPct, toSellPct };
    }

    case "donchian_breakout": {
      const high = n(ind.channel_high);
      const low = n(ind.channel_low);
      if (high === null || low === null) return NONE;
      // The strategy only fires on the TRANSITION into a breakout (see
      // donchian_breakout.py's prev_breakout_state), so a side price has
      // ALREADY broken through has no reachable signal -- price would have
      // to fall back inside the channel first. Reporting the (negative)
      // distance back to that level would read as "price needs to fall to
      // trigger a BUY": unreachable and backwards.
      return {
        toBuyPct: price > high ? null : pctMove(high, price),
        toSellPct: price < low ? null : pctMove(low, price),
      };
    }

    case "bollinger_reversion": {
      const upper = n(ind.upper_band);
      const lower = n(ind.lower_band);
      if (upper === null || lower === null) return NONE;
      if (price < lower) return { toBuyPct: pctMove(lower, price), toSellPct: null };
      if (price > upper) return { toBuyPct: null, toSellPct: pctMove(upper, price) };
      return NONE;
    }

    case "vwap_session_bounce": {
      const vwap = n(ind.vwap);
      const cprBottom = n(ind.cpr_bottom);
      const cprTop = n(ind.cpr_top);
      if (vwap === null || cprBottom === null || cprTop === null) return NONE;
      if (price > cprTop) {
        // Bullish bias day -- only a BUY (a fresh cross back above VWAP) is
        // reachable; SELL would need the bias to flip entirely first.
        return price > vwap ? NONE : { toBuyPct: pctMove(vwap, price), toSellPct: null };
      }
      if (price < cprBottom) {
        return price < vwap ? NONE : { toBuyPct: null, toSellPct: pctMove(vwap, price) };
      }
      return NONE;
    }

    default:
      return NONE;
  }
}
