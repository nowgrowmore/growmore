"""Print the headline variants on the cached series -- the before/after ruler.

    python -m research.dailydata.headline

Used to measure what the Phase 0 same-bar-lookahead fix in risk/wrapper.py
actually changed. Run it with the fix stashed and again with it applied; the
cache guarantees both runs see byte-identical bars, so every difference is the
code change and nothing else.
"""
from __future__ import annotations

import sys

from research.dailydata.fetch import load_meta
from research.dailydata.runner import HEADER, risk_managed, run_variant

MACD_FAST = {"fast_period": 5, "slow_period": 13, "signal_period": 5}
MACD_SLOW = {"fast_period": 12, "slow_period": 26, "signal_period": 9}

#: (symbol, strategy_name, params, label)
HEADLINES = [
    ("GOLDM", "risk_managed", risk_managed("ensemble_trend", {"min_agreement": 3}, 2.0, 3.0),
     "rm ensemble-agree3"),
    ("GOLDM", "risk_managed", risk_managed("macd_trend", MACD_FAST, 2.0, 3.0), "rm macd5-13-5"),
    ("GOLDM", "risk_managed", risk_managed("macd_trend", MACD_SLOW, 2.0, 3.0), "rm macd12-26-9"),
    ("GOLDM", "macd_trend", MACD_FAST, "macd5-13-5 (bare)"),
    ("GOLDM", "ensemble_trend", {"min_agreement": 3}, "ensemble-agree3 (bare)"),
    ("SILVERM", "risk_managed", risk_managed("macd_trend", MACD_FAST, 2.0, 3.0), "rm macd5-13-5"),
    ("SILVERM", "risk_managed", risk_managed("macd_trend", MACD_SLOW, 2.0, 3.0), "rm macd12-26-9"),
    ("SILVERM", "risk_managed", risk_managed("ensemble_trend", {"min_agreement": 3}, 2.0, 3.0),
     "rm ensemble-agree3"),
    ("SILVERM", "macd_trend", MACD_FAST, "macd5-13-5 (bare)"),
    ("SILVERM", "ensemble_trend", {"min_agreement": 3}, "ensemble-agree3 (bare)"),
    ("ZINCMINI", "risk_managed",
     risk_managed("bollinger_reversion", {"period": 20, "num_std": 2.5}, 2.0, None),
     "rm boll20-2.5-notrail"),
]


def main(argv=None) -> int:
    meta = load_meta()
    print(HEADER)
    print("-" * len(HEADER))
    for symbol, name, params, label in HEADLINES:
        r = run_variant(symbol, name, params, label, meta=meta)
        print(r.as_row())
    return 0


if __name__ == "__main__":
    sys.exit(main())
