"""Local, file-only store for `research.mcx_options.engine.run` backtest
results.

**This must NEVER touch Neon/Postgres or `growmore_bot.persistence`.** It is
a completely separate, local-file store, mirroring `chain_cache.py`'s
"research artifacts stay under `bot/research/`, never in the production
schema" discipline (see that module and repo `CLAUDE.md`'s scope-discipline
section) -- the account owner explicitly does not want this offline
options-selling research populating the paper-trading database.

SQLite rather than parquet: unlike `chain_cache.py`'s option-chain rows
(flat, tabular, streamed day-by-day), one backtest run is a single
irregularly-shaped record -- a config, a variable-length cycle/leg log, and a
handful of summary numbers -- looked up by label, not scanned by column.
`sqlite3` (stdlib, no new dependency) models that as one row per run with
JSON blobs for the nested structures, which is simpler here than shredding
cycles/legs into a parquet-friendly flat schema for no benefit: every
consumer (`run_strategies.py`, ad-hoc notebooks) wants "give me run X back
whole", not "aggregate across runs by column".

Summary stats (CAGR, Sharpe, max drawdown, win rate, profit factor) are
computed by `compute_summary` from the `EngineResult`'s `daily_pnl` series
(turned into an equity curve against a caller-supplied `initial_capital`) and
its `cycles`' `total_pnl` (closed cycles only -- a `still_open` cycle has no
resolved outcome to score as a win or a loss). All arithmetic is delegated to
`growmore_bot/backtest/metrics.py`'s existing, already-tested functions --
this module reuses them rather than reimplementing metrics math.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Optional

from growmore_bot.backtest.metrics import (
    cagr_pct,
    max_drawdown_pct,
    profit_factor,
    sharpe_ratio,
    win_rate_pct,
)
from growmore_bot.costs import CostModel
from research.mcx_options.engine import CycleRecord, EngineConfig, EngineResult, LegRecord

OUTPUT_DIR = Path(__file__).resolve().parent.parent / ".output" / "mcx_options"
DB_PATH = OUTPUT_DIR / "results.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    label TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    cycles_json TEXT NOT NULL,
    days_json TEXT NOT NULL,
    daily_pnl_json TEXT NOT NULL,
    total_pnl REAL NOT NULL,
    skipped_entries INTEGER NOT NULL,
    initial_capital REAL NOT NULL,
    cagr_pct REAL NOT NULL,
    sharpe REAL NOT NULL,
    max_drawdown_pct REAL NOT NULL,
    win_rate_pct REAL NOT NULL,
    profit_factor REAL NOT NULL,
    final_equity REAL NOT NULL,
    equity_curve_json TEXT NOT NULL
);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(_SCHEMA)
    return conn


def _date_default(obj: Any) -> str:
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(f"not JSON serializable: {obj!r}")


def _config_to_json(config: EngineConfig) -> str:
    return json.dumps(asdict(config), default=_date_default)


def _config_from_json(text: str) -> EngineConfig:
    d = json.loads(text)
    d["option_cost_model"] = CostModel(**d["option_cost_model"])
    d["futures_cost_model"] = CostModel(**d["futures_cost_model"])
    return EngineConfig(**d)


def _cycles_to_json(cycles: list[CycleRecord]) -> str:
    return json.dumps([asdict(c) for c in cycles], default=_date_default)


def _leg_from_dict(d: dict) -> LegRecord:
    d = dict(d)
    d["date"] = date.fromisoformat(d["date"])
    return LegRecord(**d)


def _cycle_from_dict(d: dict) -> CycleRecord:
    d = dict(d)
    d["start_date"] = date.fromisoformat(d["start_date"])
    d["end_date"] = date.fromisoformat(d["end_date"]) if d.get("end_date") else None
    d["legs"] = [_leg_from_dict(leg) for leg in d["legs"]]
    return CycleRecord(**d)


def _cycles_from_json(text: str) -> list[CycleRecord]:
    return [_cycle_from_dict(d) for d in json.loads(text)]


def compute_summary(result: EngineResult, initial_capital: float) -> dict:
    """CAGR/Sharpe/max-drawdown/win-rate/profit-factor for one `EngineResult`,
    plus the equity curve they were derived from -- all via
    `growmore_bot/backtest/metrics.py`'s existing functions, not
    reimplemented here.

    The equity curve is `initial_capital` plus the cumulative sum of
    `result.daily_pnl` (one entry per trading day, `len(days) + 1` points
    including the starting capital). Win rate / profit factor score each
    CLOSED cycle's `total_pnl` as one trade; a cycle `run()` left
    `"still_open"` at the end of the window has no resolved outcome and is
    excluded, the same way an open position is excluded from a live win-rate
    count.
    """
    equity_curve = [initial_capital]
    for pnl in result.daily_pnl:
        equity_curve.append(equity_curve[-1] + pnl)
    returns = [(b / a - 1) for a, b in zip(equity_curve, equity_curve[1:]) if a != 0]
    years = (
        max((result.days[-1] - result.days[0]).days / 365.25, 0.0) if result.days else 0.0
    )
    trade_pnls = [
        c.total_pnl for c in result.cycles if c.outcome and c.outcome != "still_open"
    ]
    final_equity = equity_curve[-1]
    return {
        "cagr_pct": cagr_pct(initial_capital, final_equity, years) if years else 0.0,
        "sharpe": sharpe_ratio(returns),
        "max_drawdown_pct": max_drawdown_pct(equity_curve),
        "win_rate_pct": win_rate_pct(trade_pnls),
        "profit_factor": profit_factor(trade_pnls),
        "final_equity": final_equity,
        "equity_curve": equity_curve,
    }


def save_run(
    label: str,
    config: EngineConfig,
    result: EngineResult,
    initial_capital: float = 1_000_000.0,
    db_path: Path = DB_PATH,
) -> dict:
    """Persist one backtest run keyed by `label`, overwriting any existing
    run with the same label. Returns the computed summary dict (see
    `compute_summary`)."""
    summary = compute_summary(result, initial_capital)
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO runs (
                    label, config_json, cycles_json, days_json, daily_pnl_json,
                    total_pnl, skipped_entries, initial_capital,
                    cagr_pct, sharpe, max_drawdown_pct, win_rate_pct,
                    profit_factor, final_equity, equity_curve_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(label) DO UPDATE SET
                    config_json=excluded.config_json,
                    cycles_json=excluded.cycles_json,
                    days_json=excluded.days_json,
                    daily_pnl_json=excluded.daily_pnl_json,
                    total_pnl=excluded.total_pnl,
                    skipped_entries=excluded.skipped_entries,
                    initial_capital=excluded.initial_capital,
                    cagr_pct=excluded.cagr_pct,
                    sharpe=excluded.sharpe,
                    max_drawdown_pct=excluded.max_drawdown_pct,
                    win_rate_pct=excluded.win_rate_pct,
                    profit_factor=excluded.profit_factor,
                    final_equity=excluded.final_equity,
                    equity_curve_json=excluded.equity_curve_json
                """,
                (
                    label,
                    _config_to_json(config),
                    _cycles_to_json(result.cycles),
                    json.dumps([d.isoformat() for d in result.days]),
                    json.dumps(result.daily_pnl),
                    result.total_pnl,
                    result.skipped_entries,
                    initial_capital,
                    summary["cagr_pct"],
                    summary["sharpe"],
                    summary["max_drawdown_pct"],
                    summary["win_rate_pct"],
                    summary["profit_factor"],
                    summary["final_equity"],
                    json.dumps(summary["equity_curve"]),
                ),
            )
    finally:
        conn.close()
    return summary


def load_run(label: str, db_path: Path = DB_PATH) -> Optional[dict]:
    """Load one saved run back, or `None` if `label` was never saved.

    Returns a dict with keys: `config` (`EngineConfig`), `cycles`
    (`list[CycleRecord]`), `days` (`list[date]`), `daily_pnl` (`list[float]`),
    `total_pnl`, `skipped_entries`, `initial_capital`, and `summary` (the
    dict `compute_summary`/`save_run` returned, including `equity_curve`).
    """
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM runs WHERE label = ?", (label,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    columns = [
        "label", "config_json", "cycles_json", "days_json", "daily_pnl_json",
        "total_pnl", "skipped_entries", "initial_capital",
        "cagr_pct", "sharpe", "max_drawdown_pct", "win_rate_pct",
        "profit_factor", "final_equity", "equity_curve_json",
    ]
    data = dict(zip(columns, row))
    return {
        "label": data["label"],
        "config": _config_from_json(data["config_json"]),
        "cycles": _cycles_from_json(data["cycles_json"]),
        "days": [date.fromisoformat(d) for d in json.loads(data["days_json"])],
        "daily_pnl": json.loads(data["daily_pnl_json"]),
        "total_pnl": data["total_pnl"],
        "skipped_entries": data["skipped_entries"],
        "initial_capital": data["initial_capital"],
        "summary": {
            "cagr_pct": data["cagr_pct"],
            "sharpe": data["sharpe"],
            "max_drawdown_pct": data["max_drawdown_pct"],
            "win_rate_pct": data["win_rate_pct"],
            "profit_factor": data["profit_factor"],
            "final_equity": data["final_equity"],
            "equity_curve": json.loads(data["equity_curve_json"]),
        },
    }


def list_runs(db_path: Path = DB_PATH) -> list[str]:
    """Every label currently saved in the store."""
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT label FROM runs").fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


__all__ = [
    "OUTPUT_DIR", "DB_PATH", "compute_summary", "save_run", "load_run", "list_runs",
]
