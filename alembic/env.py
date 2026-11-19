"""Alembic migration environment wired to the app's Settings and Base.

The database URL comes from `app.config.settings` (the `.env` file) and is
converted for asyncpg by `app.db`, so migrations connect exactly like the app.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection

import app.models  # noqa: F401  (registers every model on Base.metadata)
from alembic import context
from app.config import settings
from app.db import Base, create_db_engine, to_asyncpg_url

# The Alembic Config object, giving access to values in alembic.ini.
config = context.config

# Set up Python logging from the [loggers] sections of alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Autogenerate compares the live database against this metadata.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode: print the SQL instead of executing it.

    Only a URL is needed, no live connection.
    """
    url = to_asyncpg_url(settings.database_url.get_secret_value()).render_as_string(
        hide_password=False
    )
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Configure the migration context on a connection and run the migrations.

    Args:
        connection: A sync-style connection handed over by ``run_sync``.
    """
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Open an async connection to Neon and run the migrations on it."""
    # Same Neon fixes as the app, but NullPool: a one-off CLI command has no use
    # for a connection pool.
    connectable = create_db_engine(
        settings.database_url.get_secret_value(), poolclass=pool.NullPool
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against the live database."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
