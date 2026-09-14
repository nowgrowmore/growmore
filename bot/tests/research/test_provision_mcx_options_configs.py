"""TDD for research.provision_mcx_options_configs -- the idempotent
--dry-run/--apply CLI that creates/verifies MCXOptionsConfig rows for
GOLDM and SILVERM.

Mirrors research/provision_configs.py's own upsert discipline (see that
module's docstring): never duplicates a row on repeat runs, ALWAYS creates
new rows with enabled=False, and a re-run with different seed values never
clobbers an already-manually-enabled=True row's `enabled` field.

Uses an in-memory SQLite engine via growmore_bot.persistence.db's
monkeypatchable module-level `_engine`/`_SessionFactory` (same technique as
tests/unit/test_db.py) -- this test NEVER touches a real/remote database.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from growmore_bot.persistence import db as db_module
from growmore_bot.persistence.models import Base, MCXOptionsConfig, Strategy
from research.provision_mcx_options_configs import (
    CONSOLIDATING_TARGET_DELTA,
    LOTS,
    STRATEGY_NAME,
    STRATEGY_VERSION,
    TREND_FAVORABLE_TARGET_DELTA,
    WANTED_SYMBOLS,
    main,
)


def _sqlite_session_factory(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "_engine", engine)
    monkeypatch.setattr(db_module, "_SessionFactory", sessionmaker(bind=engine, future=True))
    return engine


def _configs(engine):
    with sessionmaker(bind=engine, future=True)() as session:
        return session.query(MCXOptionsConfig).all()


def _strategies(engine):
    with sessionmaker(bind=engine, future=True)() as session:
        return session.query(Strategy).all()


def test_dry_run_writes_nothing(monkeypatch):
    engine = _sqlite_session_factory(monkeypatch)

    rc = main([])  # no --apply flag given -> dry run
    assert rc == 0

    assert _configs(engine) == []
    assert _strategies(engine) == []


def test_apply_creates_one_disabled_paper_config_per_symbol(monkeypatch):
    engine = _sqlite_session_factory(monkeypatch)

    rc = main(["--apply"])
    assert rc == 0

    configs = _configs(engine)
    assert {c.symbol for c in configs} == set(WANTED_SYMBOLS)
    for c in configs:
        assert c.enabled is False
        assert c.mode == "paper"
        assert c.lots == LOTS
        assert float(c.consolidating_target_delta) == CONSOLIDATING_TARGET_DELTA
        assert float(c.trend_favorable_target_delta) == TREND_FAVORABLE_TARGET_DELTA

    strategies = _strategies(engine)
    assert len(strategies) == 1
    assert strategies[0].name == STRATEGY_NAME
    assert strategies[0].version == STRATEGY_VERSION


def test_apply_is_idempotent_and_never_duplicates_rows(monkeypatch):
    engine = _sqlite_session_factory(monkeypatch)

    main(["--apply"])
    main(["--apply"])

    assert len(_configs(engine)) == len(WANTED_SYMBOLS)
    assert len(_strategies(engine)) == 1


def test_apply_never_sets_enabled_true(monkeypatch):
    engine = _sqlite_session_factory(monkeypatch)

    main(["--apply"])
    main(["--apply"])

    assert all(c.enabled is False for c in _configs(engine))


def test_rerun_does_not_clobber_a_manually_enabled_row(monkeypatch):
    engine = _sqlite_session_factory(monkeypatch)
    main(["--apply"])

    # Simulate the account owner's separate, deliberate manual DB write.
    with sessionmaker(bind=engine, future=True)() as session:
        cfg = session.query(MCXOptionsConfig).filter_by(symbol="GOLDM").one()
        cfg.enabled = True
        session.commit()

    main(["--apply"])

    with sessionmaker(bind=engine, future=True)() as session:
        cfg = session.query(MCXOptionsConfig).filter_by(symbol="GOLDM").one()
        assert cfg.enabled is True


def test_rerun_with_different_seed_values_updates_tunables_but_not_enabled(monkeypatch):
    engine = _sqlite_session_factory(monkeypatch)
    main(["--apply"])

    with sessionmaker(bind=engine, future=True)() as session:
        cfg = session.query(MCXOptionsConfig).filter_by(symbol="SILVERM").one()
        cfg.enabled = True
        cfg.consolidating_target_delta = 0.10  # a manual tweak the owner made
        session.commit()

    main(["--apply"])  # re-run with the script's own (different) seed values

    with sessionmaker(bind=engine, future=True)() as session:
        cfg = session.query(MCXOptionsConfig).filter_by(symbol="SILVERM").one()
        assert cfg.enabled is True  # untouched
        assert float(cfg.consolidating_target_delta) == CONSOLIDATING_TARGET_DELTA  # refreshed


def test_dry_run_makes_no_changes_even_when_a_strategy_row_already_exists(monkeypatch):
    engine = _sqlite_session_factory(monkeypatch)
    main(["--apply"])

    rc = main([])
    assert rc == 0

    assert len(_configs(engine)) == len(WANTED_SYMBOLS)  # unchanged, still just the applied ones
