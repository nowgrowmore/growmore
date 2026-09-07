"""Who gets offered the shared capital pool first, and in what order.

The live basket has exactly one ordering: IV percentile, descending
(`wheel_basket_engine._fill_empty_capital` sorts by `-percentiles[s]`). With
55 of the 210 F&O names in Financial Services, that ordering hands one sector
the majority of a cycle's capital far more often than chance -- the
concentration this study was asked to remove.

`round_robin_order` is the alternative: the best-ranked name in each sector,
then the second from each, and so on. It is a **permutation, not a filter** --
uncapped, every eligible name still appears, just in a different order, so a
sector result cannot be confounded with a selectivity result. Only
`max_per_sector` drops anything, and that is the point of the capped variants.

SECTOR IS NSE's exclusive 18-bucket `Industry` field. **Defence is not one of
them and cannot be**: BEL/HAL/BDL/MAZDOCK/COCHINSHIP are Capital Goods,
BHARATFORG is Automobile, SOLARINDS is Chemicals. Making Defence a 19th
exclusive bucket would pull those seven out of the sectors they belong to and
distort every count -- the argument `research/fno/sectors.py` already makes at
length. Defence exposure is REPORTED (`defence_share`), never constrained.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional

#: A symbol with no sector label gets a bucket of its own rather than joining
#: a shared "unknown" pool -- pooling would make two unrelated unlabelled
#: names compete for one slot as if they were correlated. In practice every
#: name in universe.csv is labelled (that is clause 3 of the manifest), so
#: this is a guard, not a code path anyone should hit.
def _sector_of(symbol: str, sector_by_symbol: dict[str, str]) -> str:
    label = sector_by_symbol.get(symbol)
    return label if label else f"__unlabelled__{symbol}"


def flat_order(
    eligible: Iterable[str],
    percentiles: dict[str, float],
    tiebreak: Optional[dict[str, float]] = None,
) -> list[str]:
    """Today's live ordering: pure IV percentile, descending. This is `B0`,
    the benchmark every sector variant is measured against.
    """
    tb = tiebreak or {}
    return sorted(
        eligible,
        key=lambda s: (-percentiles.get(s, 0.0), -tb.get(s, 0.0), s),
    )


def round_robin_order(
    eligible: Iterable[str],
    percentiles: dict[str, float],
    sector_by_symbol: dict[str, str],
    max_per_sector: Optional[int] = None,
    tiebreak: Optional[dict[str, float]] = None,
) -> list[str]:
    """Best of each sector, then the second of each, and so on.

    `tiebreak` (T3's relative strength) orders names WITHIN a sector only.
    Sectors themselves are ordered by their own best candidate's percentile,
    so a tiebreak can never promote a whole sector -- that would make it a
    selection signal, which is a different and untested claim.
    """
    tb = tiebreak or {}
    queues: dict[str, list[str]] = defaultdict(list)
    for symbol in eligible:
        queues[_sector_of(symbol, sector_by_symbol)].append(symbol)

    for sector, members in queues.items():
        members.sort(key=lambda s: (-percentiles.get(s, 0.0), -tb.get(s, 0.0), s))
        if max_per_sector is not None:
            del members[max_per_sector:]

    # Sector order is fixed once, by each sector's best remaining candidate,
    # so it cannot drift between passes as queues drain.
    sector_rank = sorted(
        queues,
        key=lambda sec: (-percentiles.get(queues[sec][0], 0.0), sec) if queues[sec] else (0.0, sec),
    )

    order: list[str] = []
    depth = 0
    longest = max((len(q) for q in queues.values()), default=0)
    while depth < longest:
        for sector in sector_rank:
            members = queues[sector]
            if depth < len(members):
                order.append(members[depth])
        depth += 1
    return order


def max_sector_share(
    deployed_capital_by_symbol: dict[str, float],
    sector_by_symbol: dict[str, str],
) -> float:
    """The largest fraction of deployed capital sitting in one NSE sector.

    Capital, not position count: positions are unequally sized (lots are
    derived from `strike * lot_size`), so a count-based concentration figure
    would understate the exposure that actually hurts.
    """
    total = sum(deployed_capital_by_symbol.values())
    if total <= 0:
        return 0.0
    by_sector: dict[str, float] = defaultdict(float)
    for symbol, capital in deployed_capital_by_symbol.items():
        by_sector[_sector_of(symbol, sector_by_symbol)] += capital
    return max(by_sector.values()) / total


def defence_share(
    deployed_capital_by_symbol: dict[str, float],
    is_defence_by_symbol: dict[str, bool],
) -> float:
    """Defence exposure as a fraction of deployed capital. Reported only --
    the overlay is never a constraint (see the module docstring).
    """
    total = sum(deployed_capital_by_symbol.values())
    if total <= 0:
        return 0.0
    return sum(
        c for s, c in deployed_capital_by_symbol.items() if is_defence_by_symbol.get(s)
    ) / total


__all__ = ["flat_order", "round_robin_order", "max_sector_share", "defence_share"]
