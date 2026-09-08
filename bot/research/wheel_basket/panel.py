"""One symbol's option history, reduced to exactly what the basket engine replays.

WHY THIS EXISTS. The consolidated chain cache is 889 MB on disk and RELIANCE
alone is 100 MB once loaded with object dtypes -- 210 of those will not fit in
memory, and the study runs 14 configs over the same data. So the expensive,
config-independent work (adjustment into corporate-action space, implied vol,
RSI/MACD, the tradeable filter) is done ONCE per symbol here, reduced to
compact float32 arrays, and every config then replays it in memory.

THE REDUCTION, and what it costs. Only rows for the expiry a cycle actually
trades are kept -- roughly a third of the chain -- and they are stored as dense
[day x strike] matrices rather than a long frame, which is where the real
saving is (a measured 100 MB -> 5.6 MB for RELIANCE). Nothing about which
strikes are available is discarded, so strike selection sees the same menu the
long frame would have offered.

ADJUSTED SPACE IS NOT OPTIONAL, BUT ONE FACTOR PER CYCLE IS. Bhavcopy strikes
and premiums are unadjusted; the cached cash bars are corporate-action
adjusted. Mixing them silently compares a pre-split strike against a post-split
chain, so `factors` is applied to strikes, premiums and spot alike -- but ONCE
PER CYCLE, at the decision day, not per day as
`run_strategies.to_adjusted_space` does.

That difference is forced, and it is measurable rather than theoretical. The
factor is `adjusted_close / unadjusted_close` recomputed independently each
day, so it carries float noise: RELIANCE has 30 distinct factors across 30
days, all ~0.44967. Multiplying discrete strikes by a noisy factor turned 83
real strikes into 888 near-identical ones, which destroys strike identity,
defeats the nearest-match lookup and inflated one symbol's matrices from 1.6 MB
to 24 MB. A per-cycle factor keeps strikes discrete. The cost is that a
corporate action landing mid-cycle is applied from the next cycle instead of
the day it happens -- one month of slightly stale scaling on a rare event,
against a bug that would corrupt every strike lookup in the study.

Indicators (RSI, MACD, SMA200, swing low, trailing return) still run on the
fully per-day adjusted price series, because continuity across an action is the
entire reason adjustment exists.

DECISION DAYS ARE PREVIOUS EXPIRIES. A cycle is decided on the day the previous
cycle settles -- which is what the live engine does (`run_cycle` settles legs
whose `cycle_expiry == today`, then opens new ones) -- so the first expiry in
the data can never be traded and is not counted as a cycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional

import numpy as np
import pandas as pd

from growmore_bot.strategies.registry import build_strategy
from research.stock_options.pricing import implied_vol

#: The live engine's gate, mirrored so the backtest cannot trade a contract
#: the bot would refuse. See growmore_bot.options.strike_selection.
MIN_STRIKE_VOLUME = 1.0

#: Fewer cycles than this and the symbol is UNMEASURED, not weak -- the
#: treatment docs/walk-forward-results.md gave short-history contracts.
MIN_CYCLES = 6

RSI_PERIOD = 14
MACD_PARAMS = {"fast_period": 5, "slow_period": 13, "signal_period": 5}


#: Trailing windows for the per-stock signals declared in the research doc.
SMA_SLOW = 200
SWING_LOW_WINDOW = 60
RELATIVE_STRENGTH_WINDOW = 60


@dataclass
class CycleChain:
    """Everything one monthly cycle offers, decided on `decision_day`."""

    expiry: int                     # day ordinal
    decision_day: int
    days: np.ndarray                # int32 ordinals, decision_day .. expiry
    strikes: np.ndarray             # float32, sorted ascending
    spot: np.ndarray                # float32, aligned to `days`
    put_settle: np.ndarray          # [days x strikes], NaN where unquoted
    call_settle: np.ndarray
    put_volume: np.ndarray
    call_volume: np.ndarray
    # --- decision-day signals, all computed from data strictly up to that day
    atm_iv: Optional[float] = None
    rsi: Optional[float] = None
    macd_bullish: bool = False
    sma200: Optional[float] = None
    swing_low: Optional[float] = None
    trailing_return: Optional[float] = None

    def tradeable(self, is_call: bool, day_index: int) -> list:
        """(strike, premium) pairs a real order could have been filled at."""
        settle = self.call_settle if is_call else self.put_settle
        volume = self.call_volume if is_call else self.put_volume
        row_s, row_v = settle[day_index], volume[day_index]
        ok = np.isfinite(row_s) & (row_s > 0) & (row_v >= MIN_STRIKE_VOLUME)
        return [(float(k), float(p)) for k, p in zip(self.strikes[ok], row_s[ok])]

    def settle_of(self, is_call: bool, day_index: int, strike: float) -> float:
        """Mark one leg at today's settlement price.

        Strikes are matched nearest-within-tolerance rather than by float
        equality: a leg opened before a corporate action carries a strike the
        rescaled chain no longer contains exactly, and float equality on a
        product of floats is fragile anyway.

        When the strike genuinely did not print, the fallback is INTRINSIC
        value, never the entry premium -- a stale price holds the liability at
        what it was worth a month ago and hides the move that matters. This is
        the same choice `wheel_engine._mark` makes.
        """
        spot = float(self.spot[day_index])
        if len(self.strikes):
            i = int(np.abs(self.strikes - strike).argmin())
            if abs(float(self.strikes[i]) - strike) <= max(strike * 0.001, 0.01):
                settle = (self.call_settle if is_call else self.put_settle)[day_index, i]
                if np.isfinite(settle) and settle > 0:
                    return float(settle)
        return max(spot - strike, 0.0) if is_call else max(strike - spot, 0.0)


@dataclass
class SymbolPanel:
    symbol: str
    lot_size: int
    cycles: list


def _ordinal(values) -> np.ndarray:
    return pd.to_datetime(values).values.astype("datetime64[D]").astype(np.int32)


def _indicator_series(spot_by_day: pd.Series) -> tuple[dict, dict, dict, dict, dict]:
    """RSI, MACD stance, SMA200, trailing swing low and trailing return.

    RSI and MACD are driven through the shared strategy registry rather than
    reimplemented, so they cannot drift from what the bot itself computes --
    the convention `run_strategies.rsi_by_day` and `trend_bullish_by_day`
    established. Streaming the bars in order is also what makes lookahead
    structurally impossible.
    """
    rsi_strategy = build_strategy("rsi_mean_reversion", {"period": RSI_PERIOD})
    macd_strategy = build_strategy("macd_trend", dict(MACD_PARAMS))
    rsi, macd, sma, swing, ret = {}, {}, {}, {}, {}
    closes: list[float] = []
    for day, price in spot_by_day.items():
        price = float(price)
        closes.append(price)
        bar = SimpleNamespace(
            timestamp=day, open=price, high=price, low=price, close=price, volume=0.0
        )
        rsi_strategy.on_bar(bar, None)
        macd_strategy.on_bar(bar, None)
        key = int(pd.Timestamp(day).to_datetime64().astype("datetime64[D]").astype(np.int32))

        value = rsi_strategy.debug_state().get("rsi")
        if value is not None:
            rsi[key] = float(value)
        state = macd_strategy.debug_state()
        line, signal = state.get("macd"), state.get("signal")
        macd[key] = bool(line is not None and signal is not None and line > signal)
        if len(closes) >= SMA_SLOW:
            sma[key] = sum(closes[-SMA_SLOW:]) / SMA_SLOW
        if len(closes) >= SWING_LOW_WINDOW:
            swing[key] = min(closes[-SWING_LOW_WINDOW:])
        if len(closes) > RELATIVE_STRENGTH_WINDOW:
            past = closes[-RELATIVE_STRENGTH_WINDOW - 1]
            if past > 0:
                ret[key] = price / past - 1.0
    return rsi, macd, sma, swing, ret


def _atm_iv(cycle: CycleChain, day_index: int) -> Optional[float]:
    """ATM implied vol of the near-month CALL, matching the validated metric.

    This is deliberately identical to `run_strategies.atm_iv_by_cycle` -- the
    near-month call at the strike nearest spot, among TRADEABLE strikes only,
    with `years` on a 365.25 basis. §7.1's +2.9%/yr result is attributed to
    exactly this quantity, and this study freezes the metric rather than
    re-litigating it, so an "improvement" here (averaging the put in, say)
    would quietly break that attribution.

    Bhavcopy carries no IV column; it is solved by bisection, which refuses
    rather than guesses on an arbitrage-violating print.
    """
    spot = float(cycle.spot[day_index])
    if spot <= 0:
        return None
    calls = cycle.tradeable(is_call=True, day_index=day_index)
    if not calls:
        return None
    strike, price = min(calls, key=lambda kp: (abs(kp[0] - spot), kp[0]))
    years = max(cycle.expiry - cycle.days[day_index], 0) / 365.25
    vol = implied_vol(float(price), spot, float(strike), years, "CE")
    return float(vol) if vol and vol > 0 else None


def build_symbol_panel(
    symbol: str,
    chain: Optional[pd.DataFrame] = None,
    lot_size: Optional[int] = None,
    factors: Optional[pd.Series] = None,
) -> Optional[SymbolPanel]:
    """Reduce one symbol's whole option history to its tradeable cycles.

    `chain` and `factors` are injected so the decision-shaping logic is
    testable without touching the 889 MB cache -- the same separation
    `WheelBasketEngine` uses by taking `CandidateData` rather than a Dhan
    connection. Production callers pass neither and get the cached data.
    """
    if chain is None:
        from research.stock_options import chain_cache
        from research.stock_options.run_strategies import adjustment_factors

        chain = chain_cache.load_symbol(symbol)
        if chain.empty:
            return None
        chain = chain.copy()
        chain["trade_date"] = pd.to_datetime(chain["trade_date"])
        if factors is None:
            factors = adjustment_factors(symbol, chain)
        if factors is None:
            return None

    chain = chain.copy()
    chain["trade_date"] = pd.to_datetime(chain["trade_date"])
    chain["expiry"] = pd.to_datetime(chain["expiry"])
    if factors is not None:
        chain = chain[chain["trade_date"].map(factors).notna()].copy()
        if chain.empty:
            return None

    if lot_size is None:
        lot_size = int(chain["lot_size"].replace(0, pd.NA).dropna().median() or 0)
    if not lot_size or lot_size <= 0:
        return None

    expiries = sorted(chain["expiry"].unique())
    if len(expiries) < MIN_CYCLES + 1:
        return None

    # Indicators run on the ADJUSTED price series -- that is what makes the
    # series continuous across a corporate action, which is the whole reason
    # adjustment exists. The option mechanics below are scaled separately.
    spot_by_day = chain.groupby("trade_date")["underlying"].first().sort_index()
    adjusted_spot = spot_by_day
    if factors is not None:
        adjusted_spot = (spot_by_day * spot_by_day.index.map(factors)).dropna()
    rsi, macd, sma, swing, trailing = _indicator_series(adjusted_spot)

    chain["day_ord"] = _ordinal(chain["trade_date"])
    chain["exp_ord"] = _ordinal(chain["expiry"])
    by_expiry = {e: g for e, g in chain.groupby("exp_ord")}

    cycles: list[CycleChain] = []
    for previous, expiry in zip(expiries, expiries[1:]):
        decision = int(_ordinal([previous])[0])
        expiry_ord = int(_ordinal([expiry])[0])
        group = by_expiry.get(expiry_ord)
        if group is None:
            continue
        group = group[(group["day_ord"] >= decision) & (group["day_ord"] <= expiry_ord)]
        if group.empty:
            continue

        # ONE factor for the whole cycle, taken at the decision day.
        #
        # A per-DAY factor cannot be used here, and the reason is measurable
        # rather than theoretical: the factor is adjusted_close/unadjusted_close
        # computed independently each day, so it carries float noise (RELIANCE:
        # 30 distinct factors across 30 days, all ~0.44967). Multiplying
        # discrete strikes by a noisy factor turns 83 real strikes into ~2,500
        # near-identical ones, which destroys strike identity, defeats the
        # nearest-match lookup, and inflates the dense matrices ~30x.
        #
        # The cost is that a corporate action landing MID-cycle is applied from
        # the next cycle instead of the day it happens. That is one month of
        # slightly stale scaling on a rare event, against a bug that would
        # corrupt every strike lookup in the study.
        scale = 1.0
        if factors is not None:
            decision_ts = pd.Timestamp(np.datetime64(int(decision), "D"))
            if decision_ts in factors.index:
                scale = float(factors.loc[decision_ts])
            else:
                earlier = factors.index[factors.index <= decision_ts]
                if not len(earlier):
                    continue
                scale = float(factors.loc[earlier[-1]])

        days = np.sort(group["day_ord"].unique()).astype(np.int32)
        strikes = np.sort(group["strike"].unique()).astype(np.float32)
        if decision not in set(days.tolist()) or not len(strikes):
            continue

        day_index = {d: i for i, d in enumerate(days.tolist())}
        strike_index = {round(float(k), 4): i for i, k in enumerate(strikes.tolist())}
        shape = (len(days), len(strikes))
        put_settle = np.full(shape, np.nan, dtype=np.float32)
        call_settle = np.full(shape, np.nan, dtype=np.float32)
        put_volume = np.zeros(shape, dtype=np.float32)
        call_volume = np.zeros(shape, dtype=np.float32)

        rows = group[["day_ord", "strike", "opt_type", "settle", "volume"]].to_numpy()
        for day_ord, strike, opt_type, settle, volume in rows:
            i = day_index.get(int(day_ord))
            j = strike_index.get(round(float(np.float32(strike)), 4))
            if i is None or j is None:
                continue
            if opt_type == "CE":
                call_settle[i, j], call_volume[i, j] = settle, volume
            else:
                put_settle[i, j], put_volume[i, j] = settle, volume

        spot = (
            group.groupby("day_ord")["underlying"].first()
            .reindex(days).ffill().to_numpy(dtype=np.float32)
        )
        if scale != 1.0:
            strikes = (strikes * scale).astype(np.float32)
            spot = (spot * scale).astype(np.float32)
            put_settle = (put_settle * scale).astype(np.float32)
            call_settle = (call_settle * scale).astype(np.float32)

        cycle = CycleChain(
            expiry=expiry_ord, decision_day=decision, days=days, strikes=strikes,
            spot=spot, put_settle=put_settle, call_settle=call_settle,
            put_volume=put_volume, call_volume=call_volume,
            rsi=rsi.get(decision), macd_bullish=macd.get(decision, False),
            sma200=sma.get(decision), swing_low=swing.get(decision),
            trailing_return=trailing.get(decision),
        )
        cycle.atm_iv = _atm_iv(cycle, 0)
        cycles.append(cycle)

    if len(cycles) < MIN_CYCLES:
        return None
    return SymbolPanel(symbol=symbol, lot_size=int(lot_size), cycles=cycles)


__all__ = ["CycleChain", "SymbolPanel", "build_symbol_panel", "MIN_CYCLES"]
