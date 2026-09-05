import type { GaugeMarker, GaugeReferenceLine, LevelGaugeProps } from "@/components/LevelGauge";
import { toNumber } from "./format";
import type { BotConfig } from "./types";

const CURRENT_COLOR = "var(--series-1)";

function priceGaugeRange(values: number[], padFraction = 0.15): [number, number] {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || Math.abs(max) * 0.1 || 1;
  const pad = span * padFraction;
  return [min - pad, max + pad];
}

/** Maps a bot_config's live indicators + params to a LevelGauge configuration
 * -- one function per strategy family, since each has a genuinely different
 * "what's the reference, what's current" shape. Returns null when there's
 * not enough data yet (bot hasn't ticked, or this strategy has no visual
 * mapping defined), so callers can fall back to plain text.
 */
export function buildGaugeConfig(config: BotConfig): LevelGaugeProps | null {
  const ind = config.signal_indicators;
  if (!ind) return null;
  const params = config.strategy_params ?? {};
  const ltp = config.signal_ltp !== undefined && config.signal_ltp !== null ? toNumber(config.signal_ltp) : null;

  switch (config.strategy_name) {
    case "rsi_mean_reversion": {
      const rsi = ind.rsi;
      const oversold = Number(params.oversold ?? 30);
      const overbought = Number(params.overbought ?? 70);
      if (rsi === undefined || rsi === null) return null;
      return {
        min: 0,
        max: 100,
        zones: [
          { from: 0, to: oversold, color: "var(--critical-text)" },
          { from: overbought, to: 100, color: "var(--critical-text)" },
        ],
        referenceLines: [
          { value: oversold, label: "Oversold" },
          { value: overbought, label: "Overbought" },
        ],
        markers: [{ value: Number(rsi), label: "RSI", color: CURRENT_COLOR }],
      };
    }

    case "macd_trend": {
      const macd = ind.macd;
      const signal = ind.signal;
      if (macd === undefined || macd === null || signal === undefined || signal === null) return null;
      const macdNum = Number(macd);
      const signalNum = Number(signal);
      const [min, max] = priceGaugeRange([macdNum, signalNum, 0], 0.6);
      const markers: GaugeMarker[] = [
        { value: macdNum, label: "MACD", color: CURRENT_COLOR },
        { value: signalNum, label: "Signal", color: "var(--series-2)" },
      ];
      return { min, max, referenceLines: [{ value: 0, label: "Zero line" }], markers };
    }

    case "sma_crossover": {
      const fast = ind.fast_sma;
      const slow = ind.slow_sma;
      if (fast === undefined || fast === null || slow === undefined || slow === null) return null;
      const fastNum = Number(fast);
      const slowNum = Number(slow);
      const [min, max] = priceGaugeRange([fastNum, slowNum]);
      return {
        min,
        max,
        markers: [
          { value: fastNum, label: "Fast avg", color: CURRENT_COLOR },
          { value: slowNum, label: "Slow avg", color: "var(--series-2)" },
        ],
      };
    }

    case "donchian_breakout": {
      const high = ind.channel_high;
      const low = ind.channel_low;
      if (high === undefined || high === null || low === undefined || low === null || ltp === null) return null;
      const highNum = Number(high);
      const lowNum = Number(low);
      const [min, max] = priceGaugeRange([highNum, lowNum, ltp]);
      const referenceLines: GaugeReferenceLine[] = [
        { value: lowNum, label: "Channel low" },
        { value: highNum, label: "Channel high" },
      ];
      return {
        min,
        max,
        zones: [{ from: lowNum, to: highNum, color: "var(--series-1)" }],
        referenceLines,
        markers: [{ value: ltp, label: "Price", color: CURRENT_COLOR }],
      };
    }

    case "bollinger_reversion": {
      const upper = ind.upper_band;
      const lower = ind.lower_band;
      if (upper === undefined || upper === null || lower === undefined || lower === null || ltp === null) {
        return null;
      }
      const upperNum = Number(upper);
      const lowerNum = Number(lower);
      const [min, max] = priceGaugeRange([upperNum, lowerNum, ltp]);
      return {
        min,
        max,
        zones: [
          { from: min, to: lowerNum, color: "var(--critical-text)" },
          { from: upperNum, to: max, color: "var(--critical-text)" },
        ],
        referenceLines: [
          { value: lowerNum, label: "Lower band" },
          { value: upperNum, label: "Upper band" },
        ],
        markers: [{ value: ltp, label: "Price", color: CURRENT_COLOR }],
      };
    }

    case "regime_switch": {
      const adx = ind.adx;
      if (adx === undefined || adx === null) return null;
      const enter = Number(params.adx_trend_enter ?? 25);
      const exit = Number(params.adx_trend_exit ?? 20);
      return {
        min: 0,
        max: 100,
        zones: [
          { from: 0, to: exit, color: "var(--series-3)" },
          { from: enter, to: 100, color: "var(--series-2)" },
        ],
        referenceLines: [
          { value: exit, label: "Exit trend" },
          { value: enter, label: "Enter trend" },
        ],
        markers: [{ value: Number(adx), label: "ADX", color: CURRENT_COLOR }],
      };
    }

    case "ensemble_trend": {
      const bullishVotes = ind.bullish_votes;
      const members = ind.members;
      const votesNeeded = Number(params.min_agreement ?? ind.votes_needed ?? 3);
      if (bullishVotes === undefined || bullishVotes === null || members === undefined || members === null) {
        return null;
      }
      const membersNum = Number(members);
      return {
        min: 0,
        max: membersNum,
        zones: [{ from: votesNeeded, to: membersNum, color: "var(--success-text)" }],
        referenceLines: [{ value: votesNeeded, label: "Votes needed" }],
        markers: [{ value: Number(bullishVotes), label: "Bullish votes", color: CURRENT_COLOR }],
      };
    }

    case "risk_managed": {
      // The one number that matters for an open risk-managed position is how
      // far price is from its stop. The wrapped strategy's own indicators are
      // still in signal_indicators, but the stop is what the owner needs to
      // see at a glance.
      const atr = ind.atr;
      if (atr === undefined || atr === null || ltp === null) return null;
      const atrNum = Number(atr);
      const stopMultiple = Number(params.initial_stop_atr ?? 2);
      const stop = ltp - stopMultiple * atrNum;
      const [min, max] = priceGaugeRange([stop, ltp]);
      return {
        min,
        max,
        zones: [{ from: min, to: stop, color: "var(--critical-text)" }],
        referenceLines: [{ value: stop, label: "Stop (est.)" }],
        markers: [{ value: ltp, label: "Price", color: CURRENT_COLOR }],
      };
    }

    case "vwap_session_bounce": {
      const cprBottom = ind.cpr_bottom;
      const cprPivot = ind.cpr_pivot;
      const cprTop = ind.cpr_top;
      const vwap = ind.vwap;
      if (
        cprBottom === undefined || cprBottom === null ||
        cprPivot === undefined || cprPivot === null ||
        cprTop === undefined || cprTop === null ||
        ltp === null
      ) {
        return null;
      }
      const cprBottomNum = Number(cprBottom);
      const cprPivotNum = Number(cprPivot);
      const cprTopNum = Number(cprTop);
      const vwapNum = vwap !== undefined && vwap !== null ? Number(vwap) : null;
      const values = [cprBottomNum, cprTopNum, ltp, ...(vwapNum !== null ? [vwapNum] : [])];
      const [min, max] = priceGaugeRange(values);
      const referenceLines: GaugeReferenceLine[] = [
        { value: cprBottomNum, label: "CPR bottom" },
        { value: cprPivotNum, label: "CPR pivot" },
        { value: cprTopNum, label: "CPR top" },
      ];
      if (vwapNum !== null) referenceLines.push({ value: vwapNum, label: "VWAP" });
      return {
        min,
        max,
        zones: [
          { from: min, to: cprBottomNum, color: "var(--critical-text)" },
          { from: cprTopNum, to: max, color: "var(--success-text)" },
        ],
        referenceLines,
        markers: [{ value: ltp, label: "Price", color: CURRENT_COLOR }],
      };
    }

    default:
      return null;
  }
}
