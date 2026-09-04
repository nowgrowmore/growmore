import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from growmore_bot.persistence.db import normalize_database_url
from growmore_bot.persistence.models import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
#
# `disable_existing_loggers=False` is load-bearing, not cosmetic:
# fileConfig's DEFAULT is to disable every logger that already exists, and
# this env runs in-process (the integration tests apply migrations before
# their assertions). Without it, every `growmore_bot.*` logger created by an
# earlier import went permanently silent for the rest of the process --
# which made five paper-engine logging tests fail, but ONLY when Postgres
# was available for the integration tests to run at all. Found via
# independent code review 2026-09-04.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Prefer DATABASE_URL from the environment (Neon / local Postgres) over the
# placeholder in alembic.ini -- avoids ever needing a real connection string
# in a checked-in file. Normalized to force the psycopg3 driver (project
# dependency is `psycopg[binary]`, not `psycopg2`).
_database_url = os.environ.get("DATABASE_URL")
if _database_url:
    config.set_main_option("sqlalchemy.url", normalize_database_url(_database_url))

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
