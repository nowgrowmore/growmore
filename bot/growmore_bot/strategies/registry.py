"""One place that maps a strategy NAME to a constructed strategy.

`bot_config.strategies.name` is a free-text column, so every entry point that
turns a database row (or a backtest grid entry) into a live object needs the
same lookup: the scheduler, the backtest sweep, and the walk-forward harness.
Keeping a hand-written copy in each meant a newly added strategy worked on
whichever path its author happened to test and failed on the others -- which
is how `ensemble_trend` and `risk_managed` came to be registered in the
scheduler days after they were backtestable.

Deliberately stdlib-only and import-light at module scope: the strategy
modules themselves are imported lazily inside the builders, so importing this
registry costs nothing and cannot introduce an import cycle.
"""
from __future__ import annotations

from typing import Any, Callable

from growmore_bot.strategies.base import Strategy


def _sma(params: dict) -> Strategy:
    from growmore_bot.strategies.sma_crossover import SmaCrossoverStrategy

    return SmaCrossoverStrategy(**params)


def _donchian(params: dict) -> Strategy:
    from growmore_bot.strategies.donchian_breakout import DonchianBreakoutStrategy

    return DonchianBreakoutStrategy(**params)


def _rsi(params: dict) -> Strategy:
    from growmore_bot.strategies.rsi_mean_reversion import RsiMeanReversionStrategy

    return RsiMeanReversionStrategy(**params)


def _macd(params: dict) -> Strategy:
    from growmore_bot.strategies.macd_trend import MacdTrendStrategy

    return MacdTrendStrategy(**params)


def _ensemble(params: dict) -> Strategy:
    from growmore_bot.strategies.ensemble_trend import EnsembleTrendStrategy

    return EnsembleTrendStrategy(**params)


def _bollinger(params: dict) -> Strategy:
    from growmore_bot.strategies.bollinger_reversion import BollingerReversionStrategy

    return BollingerReversionStrategy(**params)


def _regime(params: dict) -> Strategy:
    from growmore_bot.strategies.regime_switch import RegimeSwitchStrategy

    return RegimeSwitchStrategy(**params)


def _vwap_session(params: dict) -> Strategy:
    from growmore_bot.strategies.vwap_session_bounce import VwapSessionBounceStrategy

    return VwapSessionBounceStrategy(**params)


def _buy_and_hold(params: dict) -> Strategy:
    from growmore_bot.strategies.buy_and_hold import BuyAndHoldStrategy

    return BuyAndHoldStrategy(**params)


def _always_flip(params: dict) -> Strategy:
    from growmore_bot.strategies.always_flip import AlwaysFlipStrategy

    return AlwaysFlipStrategy(**params)


def _risk_managed(params: dict) -> Strategy:
    from growmore_bot.risk.wrapper import build_risk_managed

    return build_risk_managed(params)


def _ema_trend(params: dict) -> Strategy:
    from growmore_bot.strategies.ema_trend import EmaTrendStrategy

    return EmaTrendStrategy(**params)


def _vol_filtered(params: dict) -> Strategy:
    from growmore_bot.risk.vol_filter import build_vol_filtered

    return build_vol_filtered(params)


STRATEGY_BUILDERS: dict[str, Callable[[dict], Any]] = {
    "sma_crossover": _sma,
    "donchian_breakout": _donchian,
    "rsi_mean_reversion": _rsi,
    "macd_trend": _macd,
    "ensemble_trend": _ensemble,
    "bollinger_reversion": _bollinger,
    "regime_switch": _regime,
    "ema_trend": _ema_trend,
    # The benchmark, as a runnable strategy -- see buy_and_hold.py.
    "buy_and_hold": _buy_and_hold,
    "vwap_session_bounce": _vwap_session,
    # Any strategy above, plus ATR stops -- see growmore_bot.risk.wrapper.
    "risk_managed": _risk_managed,
    # Any strategy above, minus entries in the top slice of trailing realised
    # volatility -- see growmore_bot.risk.vol_filter.
    "vol_filtered": _vol_filtered,
    # Demo-only, not a real trading strategy -- see always_flip.py.
    "always_flip": _always_flip,
}

STRATEGY_NAMES = tuple(sorted(STRATEGY_BUILDERS))


def build_strategy(name: str, params: dict | None = None) -> Strategy:
    """Construct `name` from `params`.

    The params dict is COPIED before it reaches the builder: `build_risk_managed`
    pops from what it is given, and callers legitimately reuse one params dict
    across instruments in a sweep, where a mutation would silently blank every
    run after the first.
    """
    if name not in STRATEGY_BUILDERS:
        raise KeyError(
            f"Unknown strategy {name!r} -- must be one of {list(STRATEGY_NAMES)}"
        )
    return STRATEGY_BUILDERS[name](dict(params or {}))
