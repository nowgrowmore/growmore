"""Tests for growmore_bot.backtest.engine.BacktestEngine.

The critical behaviour under test: a signal generated on bar N must fill at
bar N+1's OPEN, never at bar N's own close/open -- this is what avoids
lookahead bias. A signal on the LAST bar has nothing to fill against and is
simply dropped (documented, not silently wrong).
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from growmore_bot.backtest.engine import BacktestEngine
from growmore_bot.strategies.base import Signal, SignalAction, Strategy


def _bar(day, open_, high, low, close):
    return SimpleNamespace(
        timestamp=datetime(2024, 1, day, tzinfo=timezone.utc),
        open=open_,
        high=high,
        low=low,
        close=close,
    )


class _SignalOnBarIndex(Strategy):
    """Test double: fires a fixed action on one specific bar index, HOLD otherwise."""

    def __init__(self, trigger_index: int, action: SignalAction):
        self.trigger_index = trigger_index
        self.action = action
        self._i = -1

    def on_bar(self, bar, position_state):
        self._i += 1
        if self._i == self.trigger_index:
            return Signal(action=self.action, size=1)
        return Signal(action=SignalAction.HOLD)


def test_buy_signal_on_bar_n_fills_at_bar_n_plus_1_open():
    bars = [
        _bar(1, open_=100, high=105, low=95, close=102),   # bar 0 -- signal fires here
        _bar(2, open_=110, high=112, low=108, close=111),  # bar 1 -- must fill HERE, at open=110
        _bar(3, open_=120, high=122, low=118, close=121),
    ]
    strategy = _SignalOnBarIndex(trigger_index=0, action=SignalAction.BUY)
    engine = BacktestEngine(strategy=strategy, initial_capital=100_000)

    result = engine.run(bars)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.side == "buy"
    assert trade.entry_price == pytest.approx(110)  # bar 1's OPEN, not bar 0's close (102)
    assert trade.entry_price != pytest.approx(bars[0].close)
    assert trade.entered_at == bars[1].timestamp


def test_signal_on_last_bar_has_no_next_bar_and_is_dropped():
    bars = [
        _bar(1, open_=100, high=105, low=95, close=102),
        _bar(2, open_=110, high=112, low=108, close=111),
    ]
    strategy = _SignalOnBarIndex(trigger_index=1, action=SignalAction.BUY)  # last bar
    engine = BacktestEngine(strategy=strategy, initial_capital=100_000)

    result = engine.run(bars)

    assert result.trades == []


def test_sell_closes_open_position_and_records_pnl():
    bars = [
        _bar(1, open_=100, high=105, low=95, close=102),   # BUY signal fires
        _bar(2, open_=110, high=112, low=108, close=111),  # fill entry @110; SELL signal fires
        _bar(3, open_=130, high=132, low=128, close=131),  # fill exit @130
    ]

    class BuyThenSell(Strategy):
        def __init__(self):
            self._i = -1

        def on_bar(self, bar, position_state):
            self._i += 1
            if self._i == 0:
                return Signal(action=SignalAction.BUY, size=1)
            if self._i == 1:
                return Signal(action=SignalAction.SELL, size=1)
            return Signal(action=SignalAction.HOLD)

    engine = BacktestEngine(strategy=BuyThenSell(), initial_capital=100_000)
    result = engine.run(bars)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_price == pytest.approx(110)
    assert trade.exit_price == pytest.approx(130)
    assert trade.pnl == pytest.approx(20)  # (130 - 110) * qty 1


def test_lot_size_scales_pnl_and_mark_to_market_but_not_prices():
    """Regression: BacktestEngine used to treat every instrument as "1 raw unit
    of the price series," making Sharpe/Max Drawdown incomparable across
    commodities at very different price levels and lot sizes (e.g. Gold Mini
    100g vs Copper 2500kg). `lot_size` must scale quantity (and therefore P&L
    and mark-to-market equity) without touching the recorded entry/exit prices
    themselves.
    """
    bars = [
        _bar(1, open_=100, high=105, low=95, close=102),   # BUY signal fires
        _bar(2, open_=110, high=112, low=108, close=111),  # fill entry @110; SELL signal fires
        _bar(3, open_=130, high=132, low=128, close=131),  # fill exit @130
    ]

    class BuyThenSell(Strategy):
        def __init__(self):
            self._i = -1

        def on_bar(self, bar, position_state):
            self._i += 1
            if self._i == 0:
                return Signal(action=SignalAction.BUY, size=1)
            if self._i == 1:
                return Signal(action=SignalAction.SELL, size=1)
            return Signal(action=SignalAction.HOLD)

    engine = BacktestEngine(strategy=BuyThenSell(), initial_capital=100_000, lot_size=2500)
    result = engine.run(bars)

    assert len(result.trades) == 1
    trade = result.trades[0]
    # Prices themselves are untouched by lot_size.
    assert trade.entry_price == pytest.approx(110)
    assert trade.exit_price == pytest.approx(130)
    # P&L scales by lot_size: (130 - 110) * (1 * 2500).
    assert trade.pnl == pytest.approx(20 * 2500)

    # Mark-to-market while the position is open (bar 2, before the exit fills)
    # must also reflect the full lot-scaled quantity, not 1 raw unit.
    equity_while_open = result.equity_curve[1].equity
    cash_after_entry = 100_000 - (110 * 2500)
    assert equity_while_open == pytest.approx(cash_after_entry + 111 * 2500)


def test_lot_size_defaults_to_one_for_backward_compatibility():
    bars = [
        _bar(1, open_=100, high=105, low=95, close=102),
        _bar(2, open_=110, high=112, low=108, close=111),
        _bar(3, open_=130, high=132, low=128, close=131),
    ]

    class BuyThenSell(Strategy):
        def __init__(self):
            self._i = -1

        def on_bar(self, bar, position_state):
            self._i += 1
            if self._i == 0:
                return Signal(action=SignalAction.BUY, size=1)
            if self._i == 1:
                return Signal(action=SignalAction.SELL, size=1)
            return Signal(action=SignalAction.HOLD)

    engine = BacktestEngine(strategy=BuyThenSell(), initial_capital=100_000)
    result = engine.run(bars)

    assert result.trades[0].pnl == pytest.approx(20)


def test_equity_curve_has_one_point_per_bar():
    bars = [
        _bar(1, open_=100, high=105, low=95, close=102),
        _bar(2, open_=110, high=112, low=108, close=111),
        _bar(3, open_=120, high=122, low=118, close=121),
    ]
    strategy = _SignalOnBarIndex(trigger_index=99, action=SignalAction.HOLD)
    engine = BacktestEngine(strategy=strategy, initial_capital=50_000)
    result = engine.run(bars)

    assert len(result.equity_curve) == len(bars)
    assert result.equity_curve[0].equity == pytest.approx(50_000)


# --- Transaction costs -------------------------------------------------


def _cost_bars():
    """Two-bar rise then a flat bar: BUY on bar 0 fills at bar 1's open,
    SELL on bar 1 fills at bar 2's open. Round numbers so the arithmetic
    is checkable by hand."""
    return [
        _bar(1, open_=100.0, high=101.0, low=99.0, close=100.0),
        _bar(2, open_=100.0, high=111.0, low=99.0, close=110.0),
        _bar(3, open_=110.0, high=111.0, low=109.0, close=110.0),
    ]


class _BuyThenSell(Strategy):
    def __init__(self):
        self._i = -1

    def on_bar(self, bar, position_state):
        self._i += 1
        if self._i == 0:
            return Signal(action=SignalAction.BUY, size=1)
        if self._i == 1:
            return Signal(action=SignalAction.SELL, size=1)
        return Signal(action=SignalAction.HOLD)


def test_omitting_a_cost_model_reproduces_the_original_zero_cost_behaviour():
    """Every result already in the database was measured with no costs at
    all. The default must stay bit-for-bit identical so old and new runs are
    comparable, and so none of the existing engine tests change meaning."""
    result = BacktestEngine(strategy=_BuyThenSell(), initial_capital=100_000, lot_size=10).run(
        _cost_bars()
    )
    trade = result.trades[0]
    assert trade.entry_price == pytest.approx(100.0)
    assert trade.exit_price == pytest.approx(110.0)
    assert trade.pnl == pytest.approx((110.0 - 100.0) * 10)
    assert trade.transaction_cost == pytest.approx(0.0)
    assert result.total_transaction_cost == pytest.approx(0.0)


def test_a_cost_model_moves_the_fill_prices_and_nets_the_pnl():
    """Slippage is a PRICE effect (a buy pays up, a sell gets hit down) and
    the statutory charges are a rupee deduction on top. Both have to show up,
    and gross must remain recoverable."""
    from growmore_bot.costs import CostModel

    # Deliberately simple: 1 tick of slippage, and a flat Rs 10 per leg with
    # no percentage components, so every number below is hand-checkable.
    model = CostModel(
        brokerage_per_order=10.0, brokerage_pct=1.0, exchange_txn_pct=0.0,
        ctt_sell_pct=0.0, stamp_buy_pct=0.0, sebi_pct=0.0, gst_pct=0.0,
        slippage_ticks=1.0, stop_slippage_ticks=0.0,
    )
    result = BacktestEngine(
        strategy=_BuyThenSell(), initial_capital=100_000, lot_size=10,
        cost_model=model, tick_size=1.0,
    ).run(_cost_bars())

    trade = result.trades[0]
    # Buy pays up one tick (100 -> 101), sell gets hit down one (110 -> 109).
    assert trade.entry_price == pytest.approx(101.0)
    assert trade.exit_price == pytest.approx(109.0)
    assert trade.gross_pnl == pytest.approx((109.0 - 101.0) * 10)   # 80, slippage already in the prices
    assert trade.transaction_cost == pytest.approx(20.0)            # Rs 10 a leg
    assert trade.pnl == pytest.approx(80.0 - 20.0)
    assert result.total_transaction_cost == pytest.approx(20.0)


def test_costs_are_charged_against_cash_so_the_equity_curve_is_net_too():
    from growmore_bot.costs import CostModel

    model = CostModel(
        brokerage_per_order=10.0, brokerage_pct=1.0, exchange_txn_pct=0.0,
        ctt_sell_pct=0.0, stamp_buy_pct=0.0, sebi_pct=0.0, gst_pct=0.0,
        slippage_ticks=0.0, stop_slippage_ticks=0.0,
    )
    priced = BacktestEngine(
        strategy=_BuyThenSell(), initial_capital=100_000, lot_size=10,
        cost_model=model, tick_size=1.0,
    ).run(_cost_bars())
    free = BacktestEngine(
        strategy=_BuyThenSell(), initial_capital=100_000, lot_size=10
    ).run(_cost_bars())
    # Two legs at Rs 10 each come straight off the final equity.
    assert free.final_equity - priced.final_equity == pytest.approx(20.0)


def test_the_sell_leg_is_charged_ctt_and_the_buy_leg_stamp_duty():
    """Regression: `_leg_cost` briefly inferred the side from the sign of a
    notional that is always positive, so every leg was billed as a BUY --
    silently skipping CTT (sell side only, 0.01%) and always charging stamp
    duty (buy side only, 0.002%). CTT is five times stamp duty, so that
    understates a real round trip by a material margin."""
    from growmore_bot.costs import CostModel

    ctt_only = CostModel(
        brokerage_per_order=0.0, brokerage_pct=0.0, exchange_txn_pct=0.0,
        ctt_sell_pct=0.01, stamp_buy_pct=0.0, sebi_pct=0.0, gst_pct=0.0,
        slippage_ticks=0.0, stop_slippage_ticks=0.0,
    )
    result = BacktestEngine(
        strategy=_BuyThenSell(), initial_capital=100_000, lot_size=10,
        cost_model=ctt_only, tick_size=1.0,
    ).run(_cost_bars())
    # Sell leg notional is 110 * 10 = 1100; 1% of that is 11. The buy leg
    # must contribute nothing under a sell-side-only charge.
    assert result.trades[0].transaction_cost == pytest.approx(11.0)

    stamp_only = CostModel(
        brokerage_per_order=0.0, brokerage_pct=0.0, exchange_txn_pct=0.0,
        ctt_sell_pct=0.0, stamp_buy_pct=0.01, sebi_pct=0.0, gst_pct=0.0,
        slippage_ticks=0.0, stop_slippage_ticks=0.0,
    )
    result = BacktestEngine(
        strategy=_BuyThenSell(), initial_capital=100_000, lot_size=10,
        cost_model=stamp_only, tick_size=1.0,
    ).run(_cost_bars())
    # Buy leg notional is 100 * 10 = 1000; 1% is 10, and the sell adds nothing.
    assert result.trades[0].transaction_cost == pytest.approx(10.0)
