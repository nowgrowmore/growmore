"""Risk, sizing and exit logic -- deliberately separate from the strategies.

Every strategy in `growmore_bot.strategies` answers one question: "is the
market conditions right to be long/short/flat?" None of them answers "how
much?" or "where do I get out if I'm wrong?" -- and those turn out to be
where most of the risk-adjusted return of a trend-following system actually
lives. Keeping them here rather than inside each strategy means the exit and
sizing rules are written and tested once, not eight times.

Stdlib only, same as `growmore_bot.strategies`: identical code has to run
both in the backtest and per-tick in the live/paper engines.
"""
