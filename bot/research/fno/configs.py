"""The three configs under test, and the control, as flat param dicts.

Version tags are hand-assembled labels, not a parsed grammar -- there is no
parser anywhere in this repo. They follow the dialect
`research/validation/walk_forward_run.py:BASE_GRID` uses.

**Nothing here is swept, and nothing is tuned.** Phase 6's conclusion was
that 192 backtests were only ~15 effective trials and that "adding more
parameter variants now actively costs you -- the grid should shrink, not
grow" (docs/technical-debt.md). Three declared configs plus a control is the
whole grid, chosen before any result was seen.
"""
from __future__ import annotations

MACD_5_13_5 = {"fast_period": 5, "slow_period": 13, "signal_period": 5}
ENSEMBLE_AGREE3 = {"min_agreement": 3}

#: The ATR risk block shared by all three, exactly as specified.
_ATR = {"atr_period": 14, "initial_stop_atr": 2.0, "trail_atr": 3.0}


def _risk_managed(inner_strategy: str, inner_params: dict) -> dict:
    return {"inner_strategy": inner_strategy, "inner_params": inner_params, **_ATR}


def _vol_filtered(inner_strategy: str, inner_params: dict, percentile_cap: float = 0.90) -> dict:
    """Wrap a risk-managed strategy in the volatility admission filter.

    Order matters and is deliberate: the vol filter wraps the risk layer, so
    the stop logic still sees every bar and keeps managing an open position,
    and only the decision to OPEN is gated. Exits are never vetoed.
    """
    return {
        "inner_strategy": "risk_managed",
        "inner_params": _risk_managed(inner_strategy, inner_params),
        "vol_window": 20,
        "lookback": 504,
        "percentile_cap": percentile_cap,
    }


#: (tag, registry name, params). Order is the report's order.
CONFIGS: list[tuple[str, str, dict]] = [
    ("rm-macd5-13-5-stop2-trail3", "risk_managed", _risk_managed("macd_trend", MACD_5_13_5)),
    ("rm-ensemble-agree3-stop2-trail3", "risk_managed",
     _risk_managed("ensemble_trend", ENSEMBLE_AGREE3)),
    ("vol90-rm-ensemble", "vol_filtered", _vol_filtered("ensemble_trend", ENSEMBLE_AGREE3)),
]

#: The control. Not a footnote: buy-and-hold beat the trading system on five
#: of eight MCX contracts (docs/walk-forward-results.md) and on both
#: universes of the small-cap study. Every config is scored against THIS
#: stock's own buy-and-hold over the identical bars.
BENCHMARK: tuple[str, str, dict] = ("buy-and-hold", "buy_and_hold", {})

CONFIG_TAGS = [tag for tag, _name, _params in CONFIGS]

__all__ = ["CONFIGS", "BENCHMARK", "CONFIG_TAGS", "MACD_5_13_5", "ENSEMBLE_AGREE3"]
