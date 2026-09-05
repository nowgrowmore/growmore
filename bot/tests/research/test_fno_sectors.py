"""Tests for research.fno.sectors -- attaching NSE's own macro-sector label
to the F&O universe, plus a Defence overlay.

Two real facts confirmed live 2026-09-05 shape these:

  * NSE's classification has no defence sector. Every listed defence name
    sits under "Capital Goods" (BEL, HAL, BDL, MAZDOCK, COCHINSHIP) or
    elsewhere entirely (BHARATFORG is "Automobile and Auto Components",
    SOLARINDS is "Chemicals"). So Defence cannot be a 19th mutually-exclusive
    bucket -- it has to be a non-exclusive OVERLAY, or those seven stocks get
    silently pulled out of the sectors they actually belong to.
  * The NIFTY India Defence constituent CSV is at
    `ind_niftyindiadefence_list.csv` -- WITH an underscore before "list".
    The convention every other NSE index file follows
    (`ind_niftyitlist.csv`, `ind_niftyrealtylist.csv`) 404s for this one.
"""
from __future__ import annotations

from research.fno.sectors import (
    DEFENCE_URL,
    NIFTY_500_URL,
    build_sector_map,
)

NIFTY_500_CSV = "\n".join(
    [
        "Company Name,Industry,Symbol,Series,ISIN Code",
        "Reliance Industries Ltd.,Oil Gas & Consumable Fuels,RELIANCE,EQ,INE002A01018",
        "Bharat Electronics Ltd.,Capital Goods,BEL,EQ,INE263A01024",
        "Bharat Forge Ltd.,Automobile and Auto Components,BHARATFORG,EQ,INE465A01025",
        "Tata Consultancy Services Ltd.,Information Technology,TCS,EQ,INE467B01029",
    ]
)

DEFENCE_CSV = "\n".join(
    [
        "Company Name,Industry,Symbol,Series,ISIN Code",
        "Bharat Electronics Ltd.,Capital Goods,BEL,EQ,INE263A01024",
        "Bharat Forge Ltd.,Automobile and Auto Components,BHARATFORG,EQ,INE465A01025",
        # In the defence index but NOT in the F&O universe -- must not appear.
        "Zen Technologies Ltd.,Capital Goods,ZENTEC,EQ,INE251B01027",
    ]
)


def test_the_defence_index_url_carries_the_underscore_that_nse_actually_serves():
    # ind_niftyindiadefencelist.csv (no underscore) returns 404; every other
    # index file in the same directory omits it. Pinned so a "consistency"
    # cleanup cannot silently break the fetch.
    assert DEFENCE_URL.endswith("ind_niftyindiadefence_list.csv")
    assert NIFTY_500_URL.endswith("ind_nifty500list.csv")


def test_each_stock_keeps_the_sector_nse_actually_assigns_it():
    sectors = build_sector_map(NIFTY_500_CSV, DEFENCE_CSV, {"RELIANCE", "BEL", "BHARATFORG", "TCS"})
    assert sectors["RELIANCE"].nse_industry == "Oil Gas & Consumable Fuels"
    assert sectors["TCS"].nse_industry == "Information Technology"


def test_defence_is_an_overlay_not_a_bucket():
    sectors = build_sector_map(NIFTY_500_CSV, DEFENCE_CSV, {"RELIANCE", "BEL", "BHARATFORG", "TCS"})
    # BEL is Capital Goods AND Defence -- it does not leave Capital Goods.
    assert sectors["BEL"].nse_industry == "Capital Goods"
    assert sectors["BEL"].is_defence is True
    # And the overlay spans sectors: BHARATFORG is an auto stock that is also defence.
    assert sectors["BHARATFORG"].nse_industry == "Automobile and Auto Components"
    assert sectors["BHARATFORG"].is_defence is True
    assert sectors["RELIANCE"].is_defence is False


def test_defence_names_outside_the_requested_universe_are_dropped():
    sectors = build_sector_map(NIFTY_500_CSV, DEFENCE_CSV, {"RELIANCE", "BEL", "BHARATFORG", "TCS"})
    assert "ZENTEC" not in sectors


def test_a_symbol_absent_from_the_nifty_500_gets_no_entry():
    # This is the clause that excludes the 18 NSETEST symbols: they have
    # FUTSTK rows and real equity security_ids, but no index membership.
    sectors = build_sector_map(NIFTY_500_CSV, DEFENCE_CSV, {"RELIANCE", "011NSETEST"})
    assert "011NSETEST" not in sectors
    assert "RELIANCE" in sectors
