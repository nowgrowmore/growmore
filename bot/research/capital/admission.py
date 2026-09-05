"""CLI: what does one lot of each MCX commodity actually cost, and how much
leverage is the backtest quietly running on it?

Why this exists. `BacktestEngine` trades exactly 1 lot per signal
(`backtest/engine.py`) against one flat `Settings.default_virtual_capital`
for every instrument in the sweep. But MCX lot notionals span more than an
order of magnitude -- a Crude Oil Mini lot is a fraction of the account while
a Copper lot is many times it. So the sweep silently ran a wildly different
amount of leverage per instrument, which makes `docs/backtest-results.md`'s
CAGR column substantially a ranking of CONTRACT SIZE rather than of edge.
`docs/backtest-results.md` already notes this for Aluminium Mini's low CAGR
but never draws the inverse conclusion about Copper's high one.

This script prints the numbers needed to settle that, and to decide which
instruments an account of a given size can trade at a sane risk budget at
all -- given the hard constraint that the minimum tradeable size is ONE whole
lot, so "size to constant volatility" is not available here.

Read-only throughout: live quotes and historical bars via the Data-API-only
DhanClient, or (with --from-db) the most recent exit price already stored in
`backtest_trades`. Places no orders and writes nothing.

`--from-db` exists because the Dhan access token is only guaranteed fresh on
the VPS that actually runs the bot -- refreshing it from a second machine
would consume a TOTP and could invalidate the running bot's session. The
prices already in `backtest_trades` are real MCX closes, good to well within
the few percent this table needs, and need no credentials at all. The
trade-off is no ATR column: the database stores trades, not bars.

Usage (from bot/):
    python -m research.capital.admission
    python -m research.capital.admission --from-db
    python -m research.capital.admission --capital 250000 --capital 1000000
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Sequence

from growmore_bot.broker.dhan_client import DhanClient
from growmore_bot.config import DEFAULT_COMMODITY_UNIVERSE, Settings
from growmore_bot.indicators import AtrCalculator
from growmore_bot.risk.sizing import leverage_at_1_lot, notional_per_lot

# Enough calendar days to comfortably contain 14+ MCX trading days of history
# for the ATR warm-up, with room for weekends and holidays.
ATR_LOOKBACK_DAYS = 90
ATR_PERIOD = 14

# A stop this many ATRs away is the convention the risk layer will use; the
# rupee figure it implies per lot is what a risk budget actually has to cover.
STOP_ATR_MULTIPLE = 2.0


@dataclass(frozen=True)
class InstrumentAdmission:
    symbol: str
    price: float
    price_source: str  # "quote" | "last_daily_bar"
    lot_size: int
    notional: float
    atr: Optional[float]
    risk_per_lot: Optional[float]  # rupees at risk if stopped out at STOP_ATR_MULTIPLE x ATR


def _price_for(client: DhanClient, instrument) -> tuple[Optional[float], str]:
    """Live LTP when the market is open, else the most recent daily close.

    Falling back matters: this is a planning tool that has to be runnable
    outside MCX hours, and a stale close is perfectly adequate for a notional
    that only needs to be right to a few percent.
    """
    try:
        return float(client.get_quote(instrument).ltp), "quote"
    except Exception:
        pass
    bars = _recent_bars(client, instrument)
    if bars:
        return float(bars[-1].close), "last_daily_bar"
    return None, "unavailable"


def _recent_bars(client: DhanClient, instrument) -> list:
    to_date = date.today()
    from_date = to_date - timedelta(days=ATR_LOOKBACK_DAYS)
    try:
        return client.get_historical_ohlc(
            instrument,
            from_date=from_date.isoformat(),
            to_date=to_date.isoformat(),
            interval="day",
        )
    except Exception:
        return []


def _atr_for(client: DhanClient, instrument) -> Optional[float]:
    calc = AtrCalculator(period=ATR_PERIOD)
    value = None
    for bar in _recent_bars(client, instrument):
        value = calc.update(bar) or value
    return value


def build_admission(client: DhanClient, instrument) -> Optional[InstrumentAdmission]:
    price, source = _price_for(client, instrument)
    if price is None:
        return None
    atr = _atr_for(client, instrument)
    risk = (
        atr * STOP_ATR_MULTIPLE * instrument.lot_size if atr is not None else None
    )
    return InstrumentAdmission(
        symbol=instrument.symbol,
        price=price,
        price_source=source,
        lot_size=instrument.lot_size,
        notional=notional_per_lot(price, instrument.lot_size),
        atr=atr,
        risk_per_lot=risk,
    )


def admissions_from_db() -> list[InstrumentAdmission]:
    """Price every instrument off the most recent `backtest_trades.exit_price`
    already in the database. No Dhan credentials, no network to the broker,
    and no ATR (the schema stores trades, not bars)."""
    from sqlalchemy import create_engine, text

    from growmore_bot.persistence.db import normalize_database_url

    engine = create_engine(normalize_database_url(Settings().database_url))
    query = text(
        """
        SELECT i.symbol,
               i.lot_size,
               (ARRAY_AGG(bt.exit_price ORDER BY bt.exited_at DESC))[1] AS last_price
        FROM backtest_trades bt
        JOIN backtest_runs br ON br.id = bt.backtest_run_id
        JOIN instruments i ON i.id = br.instrument_id
        WHERE bt.exit_price IS NOT NULL
        GROUP BY i.symbol, i.lot_size
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()
    return [
        InstrumentAdmission(
            symbol=symbol,
            price=float(last_price),
            price_source="last_backtest_trade",
            lot_size=int(lot_size),
            notional=notional_per_lot(float(last_price), int(lot_size)),
            atr=None,
            risk_per_lot=None,
        )
        for symbol, lot_size, last_price in rows
    ]


def render(rows: Sequence[InstrumentAdmission], capitals: Sequence[float]) -> str:
    """The table. Sorted by notional so the leverage spread is the first
    thing visible."""
    rows = sorted(rows, key=lambda r: r.notional)
    lines: list[str] = []
    header = f"{'symbol':<11}{'price':>12}{'lot':>7}{'notional':>14}{'ATR(14)':>10}{'risk/lot':>11}"
    for capital in capitals:
        header += f"{'lev@' + _lakh(capital):>11}"
    lines.append(header)
    lines.append("-" * len(header))
    for r in rows:
        atr = f"{r.atr:,.2f}" if r.atr is not None else "n/a"
        risk = f"{r.risk_per_lot:,.0f}" if r.risk_per_lot is not None else "n/a"
        line = (
            f"{r.symbol:<11}{r.price:>12,.2f}{r.lot_size:>7}"
            f"{r.notional:>14,.0f}{atr:>10}{risk:>11}"
        )
        for capital in capitals:
            line += f"{leverage_at_1_lot(capital, r.price, r.lot_size):>10.2f}x"
        lines.append(line)

    lines.append("")
    lines.append("Risk budget at 1 lot (rupees at risk / capital), stop = "
                 f"{STOP_ATR_MULTIPLE:g}x ATR({ATR_PERIOD}):")
    budget_header = f"{'symbol':<11}" + "".join(f"{_lakh(c):>12}" for c in capitals)
    lines.append(budget_header)
    lines.append("-" * len(budget_header))
    for r in rows:
        line = f"{r.symbol:<11}"
        for capital in capitals:
            if r.risk_per_lot is None:
                line += f"{'n/a':>12}"
            else:
                line += f"{r.risk_per_lot / capital * 100:>11.1f}%"
        lines.append(line)

    sources = {r.price_source for r in rows}
    if sources != {"quote"}:
        lines.append("")
        lines.append("Price source: " + ", ".join(sorted(sources)) +
                     " (a live quote was unavailable for at least one instrument; a recent "
                     "close is accurate enough for a notional, but re-run against live "
                     "quotes before acting on the risk-budget rows).")
    return "\n".join(lines)


def _lakh(capital: float) -> str:
    return f"{capital / 100_000:g}L"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-db",
        action="store_true",
        help="Price off the most recent stored backtest trade instead of a live Dhan quote "
        "(no credentials needed; omits the ATR column).",
    )
    parser.add_argument(
        "--capital",
        type=float,
        action="append",
        help="Account size to evaluate against; repeatable. Defaults to 2.5L, 5L and 25L.",
    )
    args = parser.parse_args(argv)
    capitals = args.capital or [250_000.0, 500_000.0, 2_500_000.0]

    if args.from_db:
        rows = admissions_from_db()
        if not rows:
            print("No priced instrument found in backtest_trades.", file=sys.stderr)
            return 1
        print(render(rows, capitals))
        return 0

    settings = Settings()
    client = DhanClient(
        client_id=settings.dhan_client_id, access_token=settings.dhan_access_token
    )
    client.refresh_access_token_if_needed()

    rows: list[InstrumentAdmission] = []
    for instrument in DEFAULT_COMMODITY_UNIVERSE:
        row = build_admission(client, instrument)
        if row is None:
            print(f"No price available for {instrument.symbol} -- skipped", file=sys.stderr)
            continue
        rows.append(row)

    if not rows:
        print("No instrument could be priced -- check DHAN_* credentials.", file=sys.stderr)
        return 1

    print(render(rows, capitals))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["InstrumentAdmission", "admissions_from_db", "build_admission", "render", "main"]
