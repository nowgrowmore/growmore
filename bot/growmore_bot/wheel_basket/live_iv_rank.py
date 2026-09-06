"""Builds one stock's live CandidateData from Dhan's real-time option chain
and historical daily bars.

Live analog of this session's research/stock_options/iv_rank.py (average
ATM IV, the validated selection metric from docs/stock-options-results.md
Sec 7.1) and research/stock_options/run_strategies.py's rsi_by_day /
trend_bullish_by_day (the shared indicator registry, not a reimplementation)
-- but computed from ONE live snapshot rather than a historical day-by-day
series, since there's only "now" to rank stocks by in production.

Response shape for `DhanClient.get_option_chain` is not yet verified
against a real Dhan call from this codebase (see docs/pending-actions.md) --
this module only depends on the already-parsed `OptionChainSnapshot`
dataclass, so a shape correction stays isolated to dhan_client.py.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from growmore_bot.broker.dhan_client import DhanClient, OptionChainSnapshot
from growmore_bot.strategies.registry import build_strategy
from growmore_bot.wheel_basket.wheel_basket_engine import CandidateData

#: A strike must have actually printed to be sellable -- same liquidity gate
#: as growmore_bot.options.strike_selection.MIN_STRIKE_VOLUME.
MIN_STRIKE_VOLUME = 1

#: Trailing window fed to the indicator warm-up (RSI(14)/MACD(5,13,5) both
#: need well under this many bars to stabilize).
INDICATOR_WARMUP_DAYS = 120

MACD_PARAMS = {"fast_period": 5, "slow_period": 13, "signal_period": 5}
RSI_PARAMS = {"period": 14}


def average_atm_iv(chain: OptionChainSnapshot, spot: float) -> Optional[float]:
    """Average IV of the strike(s) nearest to spot, ignoring legs with no
    quoted IV. None (not 0.0) when nothing usable exists, e.g. an illiquid
    chain with no trades yet -- a real "can't rank this stock today"
    outcome, not a fabricated low-IV score that would silently exclude it.
    """
    if not chain.rows:
        return None
    nearest_strike = min((r.strike for r in chain.rows), key=lambda k: abs(k - spot))
    ivs = [r.iv for r in chain.rows if r.strike == nearest_strike and r.iv is not None]
    if not ivs:
        return None
    return sum(ivs) / len(ivs)


def tradeable_chain(chain: OptionChainSnapshot, opt_type: str) -> tuple[tuple[float, float], ...]:
    """(strike, ltp) pairs for one leg type, filtered to what actually
    trades -- feeds directly into CandidateData.put_chain/call_chain.
    """
    return tuple(
        (r.strike, r.ltp)
        for r in chain.rows
        if r.opt_type == opt_type and r.volume >= MIN_STRIKE_VOLUME and r.ltp > 0
    )


def _latest_debug_state(dhan_client: DhanClient, instrument: Any, name: str, params: dict) -> dict:
    """Warm up a shared-registry strategy on real historical bars and return
    its final debug_state() -- the same registry `scheduler.run._warm_up_strategy`
    uses live, so RSI/MACD here cannot drift from what the bot itself would
    compute for the same stock.
    """
    strategy = build_strategy(name, params)
    to_date = date.today()
    from_date = to_date - timedelta(days=INDICATOR_WARMUP_DAYS)
    bars = dhan_client.get_historical_ohlc(
        instrument, from_date=from_date.isoformat(), to_date=to_date.isoformat(), interval="day",
    )
    for bar in bars:
        strategy.on_bar(bar, None)
    return strategy.debug_state()


def build_candidate(
    dhan_client: DhanClient,
    instrument: Any,
    expiry: str,
    cycle_expiry: date,
) -> Optional[CandidateData]:
    """One stock's live CandidateData, or None if there's no usable IV today
    (illiquid chain / no trades yet) -- a real "skip this cycle for this
    stock" outcome, not a fabricated ranking input.
    """
    quote = dhan_client.get_quote(instrument)
    chain = dhan_client.get_option_chain(instrument, expiry=expiry)
    avg_iv = average_atm_iv(chain, quote.ltp)
    if avg_iv is None:
        return None

    rsi_state = _latest_debug_state(dhan_client, instrument, "rsi_mean_reversion", RSI_PARAMS)
    macd_state = _latest_debug_state(dhan_client, instrument, "macd_trend", MACD_PARAMS)
    macd, signal = macd_state.get("macd"), macd_state.get("signal")

    return CandidateData(
        symbol=getattr(instrument, "symbol", str(instrument)),
        spot=quote.ltp,
        avg_iv=avg_iv,
        lot_size=int(getattr(instrument, "lot_size", 1)),
        rsi=rsi_state.get("rsi"),
        macd_bullish=bool(macd is not None and signal is not None and macd > signal),
        put_chain=tradeable_chain(chain, "PE"),
        call_chain=tradeable_chain(chain, "CE"),
        cycle_expiry=cycle_expiry,
    )


__all__ = ["average_atm_iv", "tradeable_chain", "build_candidate"]
