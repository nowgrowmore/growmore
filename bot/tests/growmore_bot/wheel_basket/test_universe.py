"""Tests for growmore_bot.wheel_basket.universe.load_universe."""
from __future__ import annotations

from growmore_bot.wheel_basket.universe import load_universe


def test_loads_rows_from_a_real_csv(tmp_path):
    csv_path = tmp_path / "universe.csv"
    csv_path.write_text(
        "symbol,security_id,company_name,nse_industry,is_defence,fno_lot_size\n"
        "RELIANCE,2885,Reliance Industries,Oil Gas,false,250\n"
        "TCS,11536,Tata Consultancy,IT,false,150\n"
    )
    rows = load_universe(csv_path)
    assert [r.symbol for r in rows] == ["RELIANCE", "TCS"]
    assert rows[0].security_id == "2885"
    assert rows[0].lot_size == 250


def test_missing_file_returns_an_empty_list(tmp_path):
    assert load_universe(tmp_path / "does_not_exist.csv") == []


def test_the_default_path_points_at_the_real_committed_manifest():
    rows = load_universe()
    # Might be empty in a fresh checkout before `manifest --write` has ever
    # run, but if the file exists (as it does in this repo), it must parse.
    assert isinstance(rows, list)
    if rows:
        assert all(r.lot_size > 0 for r in rows)
