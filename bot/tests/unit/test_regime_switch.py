"""Tests for growmore_bot.strategies.regime_switch.

`_AdxCalculator`'s expected values were computed independently via pandas'
`ewm(alpha=1/period, adjust=False)` on a synthetic OHLC series (not derived
from this module's own code) -- see the module docstring for exactly which
Wilder-smoothing convention this matches (ewm-from-the-first-bar, not the
classical SMA-seeded variant); Wilder ADX by hand for 20+ bars isn't
practical to verify any other way.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from growmore_bot.strategies.base import Signal, SignalAction, Strategy
from growmore_bot.strategies.macd_trend import MacdTrendStrategy
from growmore_bot.strategies.regime_switch import RegimeSwitchStrategy, _AdxCalculator
from growmore_bot.strategies.rsi_mean_reversion import RsiMeanReversionStrategy
from growmore_bot.strategies.vwap_ema_reversion import VwapEmaReversionStrategy


def _bar(high, low, close):
    return SimpleNamespace(high=high, low=low, close=close)


def _trend_then_chop_bars():
    bars = []
    price = 100.0
    for _ in range(20):
        price += 2.0
        bars.append(_bar(price + 1.0, price - 1.0, price))
    chop_base = price
    for i in range(15):
        wiggle = 3.0 * (1 if i % 2 == 0 else -1)
        p = chop_base + wiggle
        bars.append(_bar(p + 1.5, p - 1.5, p))
    return bars


# Independently computed (pandas ewm reference, see module docstring), index -> expected ADX.
_EXPECTED_ADX_BY_INDEX = {
    26: 79.4989,
    27: 75.1379,
    28: 71.7824,
    29: 67.6256,
    30: 64.4391,
    31: 60.5563,
    32: 57.6042,
    33: 54.0222,
    34: 51.3314,
}


class TestAdxCalculator:
    def test_returns_none_until_enough_bars_seen(self):
        calc = _AdxCalculator(period=14)
        bars = _trend_then_chop_bars()
        for bar in bars[:26]:
            assert calc.update(bar) is None

    def test_matches_independently_computed_reference_values(self):
        calc = _AdxCalculator(period=14)
        bars = _trend_then_chop_bars()
        for i, bar in enumerate(bars):
            result = calc.update(bar)
            if i in _EXPECTED_ADX_BY_INDEX:
                assert result == pytest.approx(_EXPECTED_ADX_BY_INDEX[i], abs=1e-3)

    def test_declining_adx_reflects_the_chop_after_a_trend(self):
        calc = _AdxCalculator(period=14)
        bars = _trend_then_chop_bars()
        values = [calc.update(bar) for bar in bars]
        computed = [v for v in values if v is not None]
        assert computed == sorted(computed, reverse=True)  # monotonically declining here


class _FixedSignalStrategy(Strategy):
    """Test double: ignores bars, always returns the same signal -- lets
    regime-routing tests isolate RegimeSwitchStrategy's own logic from real
    MACD/RSI computation."""

    def __init__(self, signal: Signal):
        self._signal = signal
        self.bars_seen = 0

    def on_bar(self, bar, position_state):
        self.bars_seen += 1
        return self._signal

    def get_state_snapshot(self):
        return {"bars_seen": self.bars_seen}

    def load_state_snapshot(self, snapshot):
        self.bars_seen = snapshot.get("bars_seen", 0)


def _regime_switch_with_fixed_subs(buy_signal_strategy: str):
    """Builds a RegimeSwitchStrategy but swaps in _FixedSignalStrategy test
    doubles for both sub-strategies (monkeypatched onto the instance) so
    regime-routing can be tested deterministically regardless of what raw
    price data would make real MACD/RSI actually signal."""
    strategy = RegimeSwitchStrategy(
        ranging_strategy="rsi",
        macd_params={"fast_period": 12, "slow_period": 26, "signal_period": 9},
        ranging_params={"period": 14, "oversold": 30, "overbought": 70},
        adx_period=14,
    )
    macd_double = _FixedSignalStrategy(Signal(action=SignalAction.BUY if buy_signal_strategy == "macd" else SignalAction.HOLD))
    rsi_double = _FixedSignalStrategy(Signal(action=SignalAction.BUY if buy_signal_strategy == "rsi" else SignalAction.HOLD))
    strategy._macd = macd_double
    strategy._ranging = rsi_double
    return strategy, macd_double, rsi_double


class TestRegimeRouting:
    def test_defaults_to_ranging_before_adx_is_computable(self):
        strategy, macd_double, rsi_double = _regime_switch_with_fixed_subs(buy_signal_strategy="rsi")
        bar = _bar(101, 99, 100)
        signal = strategy.on_bar(bar, None)
        # Not enough bars for ADX yet -- no regime forced trending, defaults ranging.
        assert signal.action == SignalAction.BUY  # from the RSI double

    def test_both_sub_strategies_are_fed_every_bar_even_when_inactive(self):
        strategy, macd_double, rsi_double = _regime_switch_with_fixed_subs(buy_signal_strategy="macd")
        for bar in _trend_then_chop_bars():
            strategy.on_bar(bar, None)
        # Regardless of which regime ends up active, BOTH doubles must have
        # seen every bar -- an inactive sub-strategy's indicators must never
        # go stale, so it's ready the instant the regime flips back to it.
        assert macd_double.bars_seen == len(_trend_then_chop_bars())
        assert rsi_double.bars_seen == len(_trend_then_chop_bars())

    def test_trending_regime_routes_to_macd_signal(self):
        strategy, macd_double, rsi_double = _regime_switch_with_fixed_subs(buy_signal_strategy="macd")
        bars = _trend_then_chop_bars()
        last_signal = None
        for bar in bars[:27]:  # index 26 is the first bar with a real ADX (79.5, trending)
            last_signal = strategy.on_bar(bar, None)
        assert last_signal.action == SignalAction.BUY  # from the MACD double, not RSI

    def test_hysteresis_does_not_flip_regime_in_the_20_to_25_dead_zone(self):
        strategy, macd_double, rsi_double = _regime_switch_with_fixed_subs(buy_signal_strategy="macd")
        strategy._regime = "trending"
        strategy._adx = SimpleNamespace(update=lambda bar: 22.0)  # squarely in the 20-25 dead zone

        signal = strategy.on_bar(_bar(101, 99, 100), None)

        assert strategy._regime == "trending"  # unchanged
        assert signal.action == SignalAction.BUY  # still routed to MACD, not RSI

    def test_adx_at_or_above_enter_threshold_switches_to_trending(self):
        strategy, macd_double, rsi_double = _regime_switch_with_fixed_subs(buy_signal_strategy="macd")
        strategy._regime = "ranging"
        strategy._adx = SimpleNamespace(update=lambda bar: 25.0)

        signal = strategy.on_bar(_bar(101, 99, 100), None)

        assert strategy._regime == "trending"
        assert signal.action == SignalAction.BUY

    def test_adx_at_or_below_exit_threshold_switches_to_ranging(self):
        strategy, macd_double, rsi_double = _regime_switch_with_fixed_subs(buy_signal_strategy="rsi")
        strategy._regime = "trending"
        strategy._adx = SimpleNamespace(update=lambda bar: 20.0)

        signal = strategy.on_bar(_bar(101, 99, 100), None)

        assert strategy._regime == "ranging"
        assert signal.action == SignalAction.BUY  # now routed to RSI


class TestSnapshotRoundTrip:
    def test_restores_regime_and_both_sub_strategy_snapshots(self):
        strategy, macd_double, rsi_double = _regime_switch_with_fixed_subs(buy_signal_strategy="macd")
        strategy._regime = "trending"
        macd_double.bars_seen = 42
        rsi_double.bars_seen = 7

        snapshot = strategy.get_state_snapshot()
        assert snapshot["regime"] == "trending"
        assert snapshot["macd"] == {"bars_seen": 42}
        assert snapshot["ranging"] == {"bars_seen": 7}

        fresh_strategy, fresh_macd, fresh_rsi = _regime_switch_with_fixed_subs(buy_signal_strategy="macd")
        fresh_strategy.load_state_snapshot(snapshot)
        assert fresh_strategy._regime == "trending"
        assert fresh_macd.bars_seen == 42
        assert fresh_rsi.bars_seen == 7

    def test_load_state_snapshot_tolerates_an_empty_dict(self):
        strategy, _, _ = _regime_switch_with_fixed_subs(buy_signal_strategy="rsi")
        strategy.load_state_snapshot({})  # must not raise


class TestRealSubStrategies:
    def test_builds_real_macd_and_rsi_when_ranging_strategy_is_rsi(self):
        strategy = RegimeSwitchStrategy(
            ranging_strategy="rsi",
            macd_params={"fast_period": 12, "slow_period": 26, "signal_period": 9},
            ranging_params={"period": 14, "oversold": 30, "overbought": 70},
        )
        assert isinstance(strategy._macd, MacdTrendStrategy)
        assert isinstance(strategy._ranging, RsiMeanReversionStrategy)

    def test_builds_real_macd_and_vwap_ema_when_ranging_strategy_is_vwap_ema(self):
        strategy = RegimeSwitchStrategy(
            ranging_strategy="vwap_ema",
            macd_params={"fast_period": 12, "slow_period": 26, "signal_period": 9},
            ranging_params={"vwap_period": 20, "ema_fast": 8, "ema_slow": 21},
        )
        assert isinstance(strategy._macd, MacdTrendStrategy)
        assert isinstance(strategy._ranging, VwapEmaReversionStrategy)

    def test_unknown_ranging_strategy_name_raises(self):
        with pytest.raises(ValueError):
            RegimeSwitchStrategy(
                ranging_strategy="not_a_real_strategy",
                macd_params={"fast_period": 12, "slow_period": 26, "signal_period": 9},
                ranging_params={},
            )
