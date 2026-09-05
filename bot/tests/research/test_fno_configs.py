"""The three configs under test are pinned here, because a config that
quietly changes invalidates every number published against it.
"""
from __future__ import annotations

from growmore_bot.strategies.registry import STRATEGY_NAMES, build_strategy
from research.fno.configs import BENCHMARK, CONFIG_TAGS, CONFIGS


def test_exactly_three_configs_and_one_control():
    # The grid shrinks, it does not grow (docs/technical-debt.md, Phase 6).
    assert len(CONFIGS) == 3
    assert BENCHMARK[1] == "buy_and_hold"


def test_every_config_resolves_through_the_shared_registry():
    # Not a private builder table: the scheduler, the sweep and this research
    # must all resolve a name the same way or a strategy can work on one code
    # path and fail on another.
    for _tag, name, params in [*CONFIGS, BENCHMARK]:
        assert name in STRATEGY_NAMES
        assert build_strategy(name, params) is not None


def test_the_atr_risk_block_is_exactly_as_specified():
    for tag, _name, params in CONFIGS:
        risk = params if "atr_period" in params else params["inner_params"]
        assert risk["atr_period"] == 14, tag
        assert risk["initial_stop_atr"] == 2.0, tag
        assert risk["trail_atr"] == 3.0, tag


def test_the_vol_filter_wraps_the_risk_layer_and_not_the_other_way_round():
    # If the risk layer wrapped the filter, a vetoed bar would hide the bar
    # from the stop logic and could trap an open position.
    _tag, name, params = CONFIGS[2]
    assert name == "vol_filtered"
    assert params["inner_strategy"] == "risk_managed"
    assert params["percentile_cap"] == 0.90
    assert params["inner_params"]["inner_strategy"] == "ensemble_trend"


def test_the_tags_are_the_ones_the_report_will_print():
    assert CONFIG_TAGS == [
        "rm-macd5-13-5-stop2-trail3",
        "rm-ensemble-agree3-stop2-trail3",
        "vol90-rm-ensemble",
    ]
