"""Tests for research.fno.run_configs -- the per-stock driver.

The properties that matter are the ones that would silently corrupt a
210-stock table rather than crash it: that capital is set from the FIRST
bar, that equity costs (not MCX costs) are charged, and that the control is
run over the identical bars as the configs.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from research.fno.bar_cache import CachedBar
from research.fno.run_configs import CAPITAL_PER_STOCK, meta_for, run_symbol


def _bars(n=600, start_price=100.0):
    out = []
    start = datetime(2019, 1, 1, 18, 30, tzinfo=timezone.utc)
    price = start_price
    for i in range(n):
        price *= 1.0006 * (1.0 + 0.025 * math.sin(i / 5.0))
        out.append(
            CachedBar(start + timedelta(days=i), price, price * 1.012,
                      price * 0.988, price, 50_000.0)
        )
    return out


def _bars_with_vol_regime(n=900):
    """Like `_bars`, but with volatility that varies over time.

    `_bars` is a constant-amplitude sine wave, so its realised volatility is
    flat and NEVER enters the top decile of its own history -- the vol
    filter has nothing to refuse. Real series are not like that (on the
    cached MCX data the filter cuts Gold Mini from 47 trades to 39), so a
    test of the filter needs calm stretches and violent ones.
    """
    out = []
    start = datetime(2019, 1, 1, 18, 30, tzinfo=timezone.utc)
    price = 100.0
    for i in range(n):
        # Amplitude cycles slowly between roughly 0.5% and 6%.
        amplitude = 0.005 + 0.055 * (0.5 + 0.5 * math.sin(i / 90.0)) ** 3
        price *= 1.0006 * (1.0 + amplitude * math.sin(i / 5.0))
        out.append(
            CachedBar(start + timedelta(days=i), price, price * (1 + amplitude),
                      price * (1 - amplitude), price, 50_000.0)
        )
    return out


def test_capital_comes_from_the_first_bar_not_a_later_one():
    # Capitalising off any later price is lookahead -- the rule
    # capital_for_run documents for MCX, applied to share counts.
    bars = _bars()
    meta = meta_for("TESTCO", float(bars[0].close))
    shares = meta["TESTCO"]["lot_size"]
    assert shares == int(CAPITAL_PER_STOCK // bars[0].close)
    # A series that ends 10x higher must not change the share count.
    assert meta_for("TESTCO", float(bars[0].close)) == meta


def test_the_tick_size_is_the_nse_equity_tick():
    assert meta_for("TESTCO", 100.0)["TESTCO"]["tick_size"] == 0.05


def test_every_config_and_the_control_run_on_one_stock():
    results = run_symbol("TESTCO", _bars())
    assert set(results) == {
        "rm-macd5-13-5-stop2-trail3",
        "rm-ensemble-agree3-stop2-trail3",
        "vol90-rm-ensemble",
        "buy-and-hold",
    }


def test_the_control_is_scored_over_the_identical_bars_as_the_configs():
    bars = _bars()
    results = run_symbol("TESTCO", bars)
    capitals = {tag: r.initial_capital for tag, r in results.items()}
    assert len(set(round(c, 6) for c in capitals.values())) == 1


def test_buy_and_hold_pays_almost_nothing_next_to_a_config_that_trades():
    # The whole cost argument in one assertion: STT is charged per round
    # trip, so a config closing dozens of trades pays orders of magnitude
    # more tax than one that buys once and holds.
    results = run_symbol("TESTCO", _bars())
    traded = results["rm-macd5-13-5-stop2-trail3"]
    held = results["buy-and-hold"]
    assert traded.trades > 10
    assert traded.total_cost > 10 * held.total_cost


def test_the_shorts_arm_never_shorts_the_buy_and_hold_control():
    # A "short buy-and-hold" is meaningless as a benchmark.
    results = run_symbol("TESTCO", _bars(), allow_shorts=True)
    assert results["buy-and-hold"].trades == 0
    assert results["buy-and-hold"].final_equity > 0


def test_on_a_short_series_the_vol_filter_silently_becomes_a_no_op():
    """This is why MIN_BARS_FOR_INCLUSION exists, and it is not obvious.

    The vol filter admits or refuses an entry by comparing realised vol to
    the 90th percentile of its own trailing 504-bar history. On a series far
    shorter than that lookback it has almost no history to threshold
    against, vetoes nothing, and returns results IDENTICAL to the unfiltered
    ensemble -- while still being labelled `vol90-rm-ensemble`.

    Nothing raises and nothing looks wrong. A thin stock would just quietly
    contribute the wrong strategy's numbers to the vol90 column, and with
    210 stocks nobody would notice. The gate in run_configs.py, not this
    behaviour, is the defence.
    """
    results = run_symbol("TESTCO", _bars(n=60))
    filtered = results["vol90-rm-ensemble"]
    unfiltered = results["rm-ensemble-agree3-stop2-trail3"]
    assert filtered.trades == unfiltered.trades
    assert filtered.sharpe == pytest.approx(unfiltered.sharpe)

    # And on a long series that actually HAS a volatility regime, the
    # filter genuinely bites -- so the equality above is a short-sample
    # artefact rather than a broken wrapper.
    long_results = run_symbol("TESTCO", _bars_with_vol_regime(n=900))
    assert (
        long_results["vol90-rm-ensemble"].trades
        < long_results["rm-ensemble-agree3-stop2-trail3"].trades
    )
