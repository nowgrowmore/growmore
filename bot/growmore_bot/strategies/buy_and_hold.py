"""Buy one lot and hold it. The benchmark, as a runnable strategy.

Out-of-sample this beats the trading system on five of eight MCX contracts
(`docs/walk-forward-results.md`), which makes it the single most important
comparison in the project -- and something that important should not exist
only inside a research script. Running it as a real paper config means the
benchmark is measured by the same engine, the same cost model and the same
rollover machinery as everything it is being compared against, instead of by
a spreadsheet that quietly assumes a frictionless five-year hold.

On MCX "hold" is not passive. Futures expire, `scheduler/contract_rollover.py`
force-closes before the delivery window and repoints the instrument at the
next contract month, so the position must be re-established afterwards. Hence
the one rule here: **be long whenever flat.** A strategy that bought once
would sit in cash after its first roll and stop being buy-and-hold without
telling anyone.

Two things to know before enabling one of these:

  * **The daily-loss guard must be OFF** (`bot_config.daily_loss_limit_enabled
    = False`). Tripping it sets `config.enabled = False` permanently, and the
    guard counts REALISED P&L -- so a roll that happens to close the position
    1% underwater realises ~Rs 15,000 on a Gold Mini lot and switches the
    config off for good. Holding through drawdowns is the definition of this
    strategy; a loss limit contradicts it rather than protecting it.
  * Rolling costs about 2.4 bps a round trip, so even at monthly rolls the
    drag is ~1.5% over five years -- immaterial against the returns involved,
    but not zero, and this config is what measures it honestly.
"""
from __future__ import annotations

from typing import Any, Optional

from growmore_bot.strategies.base import Signal, SignalAction, Strategy


class BuyAndHoldStrategy(Strategy):
    """Deliberately stateless. Nothing to warm up, nothing to snapshot, and
    nothing that a scheduler restart or a warm-up replay could corrupt."""

    def __init__(self) -> None:
        pass

    def on_bar(self, bar: Any, position_state: Any) -> Signal:
        quantity = float((position_state or {}).get("quantity") or 0.0)
        if quantity != 0:
            return Signal(action=SignalAction.HOLD)
        # Flat -- either this is the first bar, or a roll/force-close just
        # flattened us. Either way, get back in.
        return Signal(action=SignalAction.BUY)

    def debug_state(self) -> dict[str, Optional[float]]:
        # Always bullish, reported through the same `stance` key the trend
        # strategies use so no consumer needs a special case for this one.
        return {"stance": 1.0}

    def get_state_snapshot(self) -> dict[str, Any]:
        return {}

    def load_state_snapshot(self, snapshot: dict[str, Any]) -> None:
        return None
