"""Async database setup for Neon PostgreSQL: engine, session factory, Base, get_db()."""

import logging
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.exceptions import UpstreamUnavailableError

logger = logging.getLogger(__name__)

# Errors that mean "could not reach the database", worth another try: Neon may
# be waking from suspend or the network may have blipped.
CONNECTION_ERRORS = (OperationalError, InterfaceError, OSError, TimeoutError)


def to_asyncpg_url(raw_url: str) -> URL:
    """Convert a Neon connection string into one the asyncpg driver accepts.

    Neon gives ``postgresql://...?sslmode=require&channel_binding=require``.
    asyncpg rejects both query parameters, so they are removed here and TLS is
    requested through ``connect_args`` instead (see `create_db_engine`).

    Args:
        raw_url: Connection string exactly as copied from the Neon console.

    Returns:
        A SQLAlchemy URL that uses the ``postgresql+asyncpg`` driver.
    """
    url = make_url(raw_url).set(drivername="postgresql+asyncpg")
    return url.difference_update_query(["sslmode", "channel_binding"])


def create_db_engine(database_url: str, **engine_kwargs: Any) -> AsyncEngine:
    """Create an async engine configured for Neon serverless PostgreSQL.

    Args:
        database_url: Neon connection string (any ``postgresql://`` form).
        **engine_kwargs: Extra options for `create_async_engine`, for example
            ``poolclass=NullPool`` for Alembic and the test suite.

    Returns:
        A configured `AsyncEngine`. No connection is opened until first use.
    """
    return create_async_engine(
        to_asyncpg_url(database_url),
        # Replaces the stripped sslmode=require: Neon only accepts TLS.
        connect_args={"ssl": "require"},
        # Neon suspends idle compute and drops its connections, so test each
        # pooled connection before use and never keep one longer than 5 minutes.
        pool_pre_ping=True,
        pool_recycle=300,
        **engine_kwargs,
    )


engine = create_db_engine(settings.database_url.get_secret_value())

# expire_on_commit=False keeps attributes loaded after commit, so a service can
# return an object it just saved without a lazy reload (which async cannot do).
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    """Parent class of every ORM model; its metadata describes all tables."""


async def get_db() -> AsyncIterator[AsyncSession]:
    """Provide one database session per request (FastAPI dependency).

    Yields:
        An `AsyncSession`, closed automatically when the request finishes.
    """
    async with SessionLocal() as session:
        yield session


async def connect_with_retries(session: AsyncSession) -> None:
    """Open the session's connection now, retrying with exponential backoff.

    A session normally connects lazily on its first query, in the middle of a
    service; connecting up front (`deps.get_session`) means a Neon outage is
    retried here, in one place, and turns into a clean 503 before any work is
    done. /health skips this: it has its own short timeout per dependency.

    Args:
        session: The new session.

    Raises:
        UpstreamUnavailableError: If the database is still unreachable after
            ``settings.db_max_retries`` attempts (503).
    """
    retrying = AsyncRetrying(
        retry=retry_if_exception_type(CONNECTION_ERRORS),
        stop=stop_after_attempt(settings.db_max_retries),
        wait=wait_exponential(multiplier=0.5, max=4),
        reraise=False,
    )
    try:
        async for attempt in retrying:
            with attempt:
                await session.connection()
    except RetryError as exc:
        logger.warning("Database unreachable after %d attempts", settings.db_max_retries)
        raise UpstreamUnavailableError() from exc
