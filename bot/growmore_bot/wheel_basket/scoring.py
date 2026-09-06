"""Stock selection scoring for the wheel-basket paper strategy.

The score is exactly the validated selection metric from
docs/stock-options-results.md Sec 7.1: a stock's cross-sectional IV
percentile rank that cycle. RSI/MACD are NOT blended into the score --
only the IV-rank restriction was backtested and shown to work (the
cross-experiment showed a D-trailing-return proxy actually LOSES to the
field at every cutoff tested), so inventing a multi-factor formula here
would not be traceable to any result. RSI/MACD are still recorded (on
`wheel_basket_selections`, via the caller) for dashboard transparency, and
RSI separately drives the covered-call basis buffer once a stock is held
(see growmore_bot.options.strike_selection.rsi_scaled_basis_buffer) --
a different decision point from stock selection.
"""
from __future__ import annotations

from typing import Optional


def iv_percentile_ranks(avg_iv_by_symbol: dict[str, float]) -> dict[str, float]:
    """Each symbol's percentile rank (0=lowest IV, 1=highest) among the
    others given -- the walk-forward selection metric from Sec 7.1's IV-rank
    experiment (`research.stock_options.iv_rank`'s method, computed live from
    a real-time option chain instead of historical bhavcopy).

    Ties share the same rank (by count of strictly-lower values) rather than
    one arbitrarily edging out the other.
    """
    if not avg_iv_by_symbol:
        return {}
    values = list(avg_iv_by_symbol.values())
    n = len(values)
    if n == 1:
        return {next(iter(avg_iv_by_symbol)): 1.0}
    return {
        symbol: sum(1 for v in values if v < iv) / n
        for symbol, iv in avg_iv_by_symbol.items()
    }


def eligible_candidates(percentiles: dict[str, float], top_frac: float) -> set[str]:
    """Symbols in the top `top_frac` of that cycle's IV percentile ranking.

    `top_frac` defaults to 0.33 at the config level (wheel_basket_configs) --
    the best risk/robustness trade-off found in the backtest sweep (0.20 had
    the biggest edge but lost a year in the sweep; 0.50 was most robust at a
    smaller edge; 0.33 kept the same 6/6 win rate while capturing most of
    0.20's extra edge).
    """
    if not percentiles or top_frac <= 0:
        return set()
    threshold = 1.0 - top_frac
    return {s for s, p in percentiles.items() if p >= threshold}


def reason_for(
    symbol: str,
    iv_percentile: float,
    rsi: Optional[float],
    macd_bullish: Optional[bool],
    selected: bool,
) -> str:
    """Human-readable "why" string for one candidate, for
    wheel_basket_selections.reason -- rendered directly as prose on the
    dashboard, not templated per strategy name the way
    dashboard/lib/signal-explain.ts is for the single-instrument MCX
    strategies (there's one basket strategy here, and the reasoning is
    inherently per-stock data, not a formula to explain).
    """
    parts = [f"IV percentile {iv_percentile:.0%}"]
    if rsi is not None:
        parts.append(f"RSI {rsi:.0f}")
    if macd_bullish is not None:
        parts.append("MACD bullish" if macd_bullish else "MACD bearish")
    verdict = "selected" if selected else "not selected"
    return f"{symbol}: {', '.join(parts)} -- {verdict}"


def should_rotate(current_score: float, best_candidate_score: float, hysteresis_pct: float) -> bool:
    """The user's "stay unless clearly better" rule: only leave the current
    stock if another candidate's score beats it by more than
    `hysteresis_pct`, avoiding churn on noise in a per-stock ranking the
    backtest's own rank-stability work (rank_stability.py) found largely
    unstable for every strategy except a weak signal on E.
    """
    return best_candidate_score > current_score * (1 + hysteresis_pct)


__all__ = ["iv_percentile_ranks", "eligible_candidates", "reason_for", "should_rotate"]
