"""Fold geometry and grid fingerprinting for out-of-sample validation.

Why this exists: every backtest number in this repo is in-sample. The sweep
runs 264 variants over one window and reports the best, and
`deflated_sharpe.py` then discounts that number for the search that produced
it. Deflation is a correction; it is not evidence. The only thing that
actually tests whether selecting a variant on past data helps you in future
data is to select on past data and measure on future data.

Geometry, fixed here rather than passed in at the call site so a re-run cannot
quietly become a different experiment:

    train 504 bars (~2 years)  select the variant
    test  126 bars (~6 months) measure it, having never seen these bars
    step  126 bars             so the test segments tile without overlap

On ~1,210 bars of Gold Mini / Silver Mini history that is FIVE out-of-sample
segments (not six -- 504 + 6*126 = 1,260 > 1,207), covering roughly the last
two and a half years. Instruments listed in 2023 (ALUMINI, CRUDEOILM,
LEADMINI, ZINCMINI) have ~880 bars and support three.

Stdlib only, consistent with the rest of growmore_bot.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

#: The declared geometry. Changing these changes the experiment, and
#: `grid_hash` plus these three numbers are what a stored run should record.
DEFAULT_TRAIN = 504
DEFAULT_TEST = 126
DEFAULT_STEP = 126


@dataclass(frozen=True)
class Fold:
    """Half-open bar-index ranges: [train_start, train_end) then
    [test_start, test_end). `train_end == test_start` always, so there is
    neither a gap (which would waste data) nor an overlap (which would leak)."""

    index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int

    @property
    def train_len(self) -> int:
        return self.train_end - self.train_start

    @property
    def test_len(self) -> int:
        return self.test_end - self.test_start


def make_folds(
    n_bars: int,
    train: int = DEFAULT_TRAIN,
    test: int = DEFAULT_TEST,
    step: int = DEFAULT_STEP,
    anchored: bool = False,
) -> list[Fold]:
    """Cut `n_bars` into walk-forward folds.

    `anchored=False` (the default) rolls a fixed-length training window, so
    every fold selects on the same amount of data and the folds are therefore
    comparable to each other. `anchored=True` expands from bar 0 instead,
    which uses more data per fold at the cost of that comparability.

    A trailing window too short for a FULL test segment is dropped rather than
    shortened: a half-length final fold is not comparable to the others and
    would silently give the most recent regime a different weight.
    """
    if train <= 0 or test <= 0 or step <= 0:
        raise ValueError(
            f"train/test/step must all be positive, got {train}/{test}/{step}"
        )

    folds: list[Fold] = []
    train_end = train
    while train_end + test <= n_bars:
        folds.append(
            Fold(
                index=len(folds),
                train_start=0 if anchored else train_end - train,
                train_end=train_end,
                test_start=train_end,
                test_end=train_end + test,
            )
        )
        train_end += step
    return folds


def grid_hash(grid: Iterable[Any]) -> str:
    """A short fingerprint of the variant grid a walk-forward run searched.

    Selection bias scales with how much you searched, so a stored result is
    only interpretable next to the grid that produced it. Dict key order must
    not change the fingerprint (it carries no meaning) but adding, removing or
    retuning a variant must.
    """
    payload = json.dumps(_canonical(list(grid)), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


__all__ = ["Fold", "make_folds", "grid_hash", "DEFAULT_TRAIN", "DEFAULT_TEST", "DEFAULT_STEP"]
