"""Goldmini / Silvermini MCX OPTION contract specs -- placeholders only.

This is the OPTIONS analogue of `growmore_bot.config.CommodityPlaceholder`,
which holds the FUTURES lot size / tick size for the same underlyings and was
verified 2026-09 against Dhan's own instrument master and MCX's published
contract-spec pages (see that module's docstring for the full story, including
the real 10x P&L bug a wrong Gold Mini lot-size unit caused live).

Nobody has done that verification for the OPTION contracts yet. In
particular, none of the following can be assumed to just equal the
corresponding futures value, and none are known/looked-up in this codebase:

  * `lot_size` -- MCX options are frequently sized in the SAME lot as their
    underlying future, but that is a fact to CONFIRM per symbol, not to
    assume. Getting this wrong reproduces exactly the kind of silent
    10x-notional bug CommodityPlaceholder's docstring describes for Gold Mini
    futures.
  * `tick_size` -- option premiums are often quoted with a finer tick than
    the underlying future (premium ticks can be smaller than futures-price
    ticks on the same exchange), so it must not be copied from
    CommodityPlaceholder either.
  * `strike_interval` -- the spacing between adjacent strikes MCX lists,
    needed later for delta-targeted strike selection. Not known here.
  * `expiry_offset_days_from_futures` -- MCX commodity options typically
    expire some number of days BEFORE their underlying futures contract
    (mirroring NSE's index/stock options expiring before their futures
    counterpart in spirit only -- the MCX mechanics and exact day count are
    not confirmed), and whether that offset is in trading days or calendar
    days is also unconfirmed.

Every numeric field below is therefore `None`, marked TODO_VERIFY in the
field comment. **DO NOT invent numbers here** -- a wrong lot/tick size
silently mis-sizes every position and P&L figure a later backtest phase
produces, and there is no fixed test against which a guess could be caught.
Fill these in only from a primary source (MCX's own contract specification
circulars, or Dhan's instrument master the way CommodityPlaceholder was), and
update this docstring with the lookup date once that happens.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MCXOptionContractSpec:
    """One MCX commodity-option contract's specs -- see module docstring.

    `symbol` names the UNDERLYING FUTURES contract this option is written on
    (e.g. "GOLDM" for Gold Mini), matching
    `growmore_bot.config.CommodityPlaceholder.symbol` so the two can be
    joined later. It is not itself unverified -- only the numeric fields
    below are.
    """

    symbol: str
    name: str
    exchange_segment: str = "MCX_COMM"

    #: TODO_VERIFY: confirm against MCX's option contract specification --
    #: do NOT assume this equals the underlying future's lot_size.
    lot_size: Optional[int] = None

    #: TODO_VERIFY: option premium tick size, in rupees per quote unit --
    #: may differ from the underlying future's tick_size.
    tick_size: Optional[float] = None

    #: TODO_VERIFY: spacing between adjacent strikes MCX lists for this
    #: contract.
    strike_interval: Optional[float] = None

    #: TODO_VERIFY: how many days before the underlying future's expiry this
    #: option contract itself expires. Unit (trading days vs. calendar days)
    #: is also unconfirmed -- do not assume either without checking a real
    #: MCX contract circular.
    expiry_offset_days_from_futures: Optional[int] = None


#: Goldmini and Silvermini only, per the phase-1 scope -- named here so later
#: phases have a fixed place to look these up, but every value is still an
#: unverified placeholder (see module docstring).
DEFAULT_MCX_OPTION_SPECS: list[MCXOptionContractSpec] = [
    MCXOptionContractSpec(symbol="GOLDM", name="Gold Mini Options"),
    MCXOptionContractSpec(symbol="SILVERM", name="Silver Mini Options"),
]


__all__ = ["MCXOptionContractSpec", "DEFAULT_MCX_OPTION_SPECS"]
