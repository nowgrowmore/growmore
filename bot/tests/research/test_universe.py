"""Tests for research.smallcap_momentum.universe -- parsing NSE's real
constituent-list CSV shape (confirmed live 2026-09-04):
'Company Name,Industry,Symbol,Series,ISIN Code'. `fetch_index_constituents_csv`
(a thin requests.get wrapper) is deliberately not unit-tested here, same
convention as growmore_bot.broker.instrument_master.fetch_instrument_master_csv.
"""
from __future__ import annotations

from research.smallcap_momentum.universe import Constituent, parse_constituents

FIXTURE_CSV = """Company Name,Industry,Symbol,Series,ISIN Code
ACME Solar Holdings Ltd.,Power,ACMESOLAR,EQ,INE622W01025
Aadhar Housing Finance Ltd.,Financial Services,AADHARHFC,EQ,INE883F01010
Aarti Industries Ltd.,Chemicals,AARTIIND,EQ,INE769A01020
"""


def test_parses_real_nse_csv_shape():
    result = parse_constituents(FIXTURE_CSV)
    assert result == [
        Constituent(symbol="ACMESOLAR", company_name="ACME Solar Holdings Ltd.", industry="Power"),
        Constituent(
            symbol="AADHARHFC",
            company_name="Aadhar Housing Finance Ltd.",
            industry="Financial Services",
        ),
        Constituent(
            symbol="AARTIIND", company_name="Aarti Industries Ltd.", industry="Chemicals"
        ),
    ]


def test_returns_empty_list_for_header_only_csv():
    assert parse_constituents("Company Name,Industry,Symbol,Series,ISIN Code\n") == []


def test_skips_blank_lines():
    csv_text = (
        "Company Name,Industry,Symbol,Series,ISIN Code\n"
        "Aarti Industries Ltd.,Chemicals,AARTIIND,EQ,INE769A01020\n"
        "\n"
    )
    result = parse_constituents(csv_text)
    assert len(result) == 1
    assert result[0].symbol == "AARTIIND"
