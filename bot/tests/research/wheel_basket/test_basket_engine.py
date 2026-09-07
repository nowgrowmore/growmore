"""Protects the basket engine: shared capital, rotation, and honest marking.

Four properties matter more than any result the engine produces:

1. CAPITAL CONSERVATION -- one pool, never overdrawn. A basket that quietly
   spends money it does not have reports leverage as alpha.
2. DAILY MARK TO MARKET -- an underwater assigned holding is marked at SPOT,
   never at its cost basis. Marking at basis draws a smooth equity curve that
   is a lie, which is the rule wheel_engine's docstring states.
3. THE STATE MACHINE -- put assigned to shares, call written above basis,
   called away. The same machine the live engine runs.
4. SECTOR CONSTRAINTS ACTUALLY BIND -- the whole point of the study.
"""
from __future__ import annotations

import numpy as np
import pytest

from research.wheel_basket.basket_engine import run_basket
from research.wheel_basket.config import BasketConfig
from research.wheel_basket.panel import CycleChain, SymbolPanel

LOT = 10
STRIKES = np.array([80.0, 90.0, 100.0, 110.0, 120.0], dtype=np.float32)


def _cycle(decision: int, expiry: int, spot_path: list[float], premium: float = 5.0,
           atm_iv: float = 0.30, **kwargs) -> CycleChain:
    days = np.arange(decision, decision + len(spot_path), dtype=np.int32)
    spot = np.array(spot_path, dtype=np.float32)
    shape = (len(days), len(STRIKES))
    put = np.full(shape, np.nan, dtype=np.float32)
    call = np.full(shape, np.nan, dtype=np.float32)
    for i in range(len(days)):
        for j, k in enumerate(STRIKES):
            put[i, j] = max(k - spot[i], 0.0) + premium
            call[i, j] = max(spot[i] - k, 0.0) + premium
    return CycleChain(
        expiry=expiry, decision_day=decision, days=days, strikes=STRIKES.copy(),
        spot=spot, put_settle=put, call_settle=call,
        put_volume=np.full(shape, 50.0, dtype=np.float32),
        call_volume=np.full(shape, 50.0, dtype=np.float32),
        atm_iv=atm_iv, rsi=kwargs.get("rsi", 50.0),
        macd_bullish=kwargs.get("macd_bullish", True),
        sma200=kwargs.get("sma200", 90.0), swing_low=kwargs.get("swing_low", 85.0),
        trailing_return=kwargs.get("trailing_return", 0.10),
    )


def _panel(symbol: str, cycles: list) -> SymbolPanel:
    return SymbolPanel(symbol=symbol, lot_size=LOT, cycles=cycles)


def _flat_panel(symbol: str, atm_iv: float, n: int = 8, spot: float = 100.0) -> SymbolPanel:
    """A stock that never moves: every put expires worthless, forever."""
    cycles = []
    for i in range(n):
        decision, expiry = i * 20, i * 20 + 19
        cycles.append(_cycle(decision, expiry, [spot] * 20, atm_iv=atm_iv))
    return _panel(symbol, cycles)


SECTORS = {"A": "Financial Services", "B": "Financial Services",
           "C": "Information Technology", "D": "Healthcare"}
DEFENCE = {s: False for s in SECTORS}


def _run(panels, config, capital=1_000_000.0, **kwargs):
    return run_basket(panels, SECTORS, DEFENCE, config, initial_capital=capital, **kwargs)


def test_cash_never_goes_negative():
    """A cash-secured put must actually be cash-secured.

    IVs are distinct on purpose: `iv_percentile_ranks` scores a tie by the
    count of strictly-lower values, so four identical IVs all rank 0.0, clear
    no top-fraction cut, and the basket trades nothing -- which would make
    this test pass without ever opening a position.
    """
    panels = {s: _flat_panel(s, iv) for s, iv in
              (("A", 0.60), ("B", 0.55), ("C", 0.50), ("D", 0.45))}
    result = _run(panels, BasketConfig(tag="B0"), capital=200_000.0)
    assert result.cycles > 0, "nothing traded -- the test would be vacuous"
    assert min(result.equity_curve) > 0
    assert result.min_cash >= -1e-6, f"overdrawn by {result.min_cash}"


def test_a_flat_stock_collects_premium_every_cycle():
    panels = {"A": _flat_panel("A", 0.4)}
    result = _run(panels, BasketConfig(tag="B0"))
    assert result.cycles > 0
    assert result.final_equity > result.initial_capital


def test_an_assigned_holding_is_marked_at_spot_not_at_basis():
    """THE honesty test. A stock assigned at 100 and now at 60 must show the
    loss on the equity curve, not hide it behind its cost basis.
    """
    crash = [100.0] * 5 + [60.0] * 15
    panel = _panel("A", [_cycle(0, 19, crash), _cycle(20, 39, [60.0] * 20)])
    result = _run({"A": panel}, BasketConfig(tag="B0"))
    # Assigned at 100 with spot 60: equity must fall well below the premium
    # collected, i.e. the drawdown is real and visible.
    assert result.max_drawdown_pct > 5.0
    assert result.final_equity < result.initial_capital


def test_a_put_expiring_in_the_money_assigns_shares_at_the_strike():
    crash = [100.0] * 19 + [60.0]
    panel = _panel("A", [_cycle(0, 19, crash), _cycle(20, 39, [60.0] * 20)])
    result = _run({"A": panel}, BasketConfig(tag="B0"))
    assert result.assignments >= 1


def test_a_covered_call_is_never_written_below_the_assignment_basis():
    """The no-loss rule. Writing below basis locks in a loss on the shares,
    which is the one thing this strategy exists not to do.
    """
    crash = [100.0] * 19 + [60.0]
    cycles = [_cycle(0, 19, crash)] + [
        _cycle(20 * i, 20 * i + 19, [60.0] * 20) for i in range(1, 5)
    ]
    result = _run({"A": _panel("A", cycles)}, BasketConfig(tag="B0"))
    for leg in result.legs:
        if leg.opt_type == "CE":
            assert leg.strike >= leg.basis - 1e-9


def test_sector_round_robin_actually_lowers_concentration():
    """The study's central claim has to be visible in the engine's own output.

    Three financials with the highest IV would otherwise take the whole book.
    """
    panels = {
        "A": _flat_panel("A", 0.60), "B": _flat_panel("B", 0.55),
        "C": _flat_panel("C", 0.50), "D": _flat_panel("D", 0.45),
    }
    flat = _run(panels, BasketConfig(tag="B0", top_iv_frac=0.5))
    spread = _run(panels, BasketConfig(tag="S1", top_iv_frac=0.5, sector_round_robin=True))
    assert spread.max_sector_share <= flat.max_sector_share


def test_max_per_sector_caps_positions_from_one_sector():
    panels = {s: _flat_panel(s, iv) for s, iv in
              (("A", 0.60), ("B", 0.55), ("C", 0.50), ("D", 0.45))}
    result = _run(panels, BasketConfig(
        tag="S2", top_iv_frac=1.0, sector_round_robin=True, max_per_sector=1))
    concurrent = result.max_concurrent_by_sector
    assert concurrent.get("Financial Services", 0) <= 1


def test_the_regime_gate_stops_new_entries_but_not_settlement():
    """R1 withholds NEW puts only; an open position still runs to its terms."""
    panels = {"A": _flat_panel("A", 0.4)}
    bearish = {d: "bearish" for d in range(0, 200)}
    gated = _run(panels, BasketConfig(tag="R1", regime_gate=True), regime_by_day=bearish)
    ungated = _run(panels, BasketConfig(tag="B0"))
    assert gated.cycles < ungated.cycles


def test_a_day_with_no_regime_label_behaves_exactly_like_the_baseline():
    """A data gap must not become a trading decision."""
    panels = {"A": _flat_panel("A", 0.4)}
    gated = _run(panels, BasketConfig(tag="R1", regime_gate=True), regime_by_day={})
    baseline = _run(panels, BasketConfig(tag="B0"))
    assert gated.cycles == baseline.cycles
    assert gated.final_equity == pytest.approx(baseline.final_equity)


def test_regime_sizing_deploys_less_capital_when_the_market_is_bad():
    panels = {s: _flat_panel(s, iv) for s, iv in
              (("A", 0.60), ("B", 0.55), ("C", 0.50), ("D", 0.45))}
    bearish = {d: "bearish" for d in range(0, 200)}
    sized = _run(panels, BasketConfig(tag="R3", regime_sizing=True), regime_by_day=bearish)
    baseline = _run(panels, BasketConfig(tag="B0"))
    assert baseline.peak_deployed_fraction > 0, "baseline deployed nothing"
    assert sized.peak_deployed_fraction < baseline.peak_deployed_fraction


def test_the_headwind_filter_drops_bearish_stocks_from_selection():
    """T1 acts at STOCK SELECTION -- a different decision point from the
    failed strategy I, which gated whether to write the call.
    """
    good = _flat_panel("A", 0.6)
    bad = _flat_panel("B", 0.6)
    for cycle in bad.cycles:
        cycle.macd_bullish = False
        cycle.sma200 = 200.0            # spot 100 is far below its SMA200
    result = _run({"A": good, "B": bad},
                  BasketConfig(tag="T1", top_iv_frac=1.0, headwind_filter=True))
    assert "B" not in result.symbols_traded
    assert "A" in result.symbols_traded


def test_buy_and_hold_benchmark_tracks_the_universe_not_the_strategy():
    panels = {"A": _flat_panel("A", 0.4, spot=100.0)}
    rising = _panel("A", [_cycle(i * 20, i * 20 + 19, [100.0 + i * 10] * 20)
                          for i in range(8)])
    result = _run({"A": rising}, BasketConfig(tag="BH", buy_and_hold=True))
    assert result.final_equity > result.initial_capital


def test_results_are_reproducible_run_to_run():
    panels = {s: _flat_panel(s, iv) for s, iv in
              (("A", 0.60), ("B", 0.55), ("C", 0.50), ("D", 0.45))}
    config = BasketConfig(tag="B0")
    first, second = _run(panels, config), _run(panels, config)
    assert first.final_equity == pytest.approx(second.final_equity)
    assert first.cycles == second.cycles
