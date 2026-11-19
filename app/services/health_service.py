"""Health check: is each dependency (Neon, Qdrant) reachable right now?"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from qdrant_client import AsyncQdrantClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.schemas import CheckStatus, HealthChecks, HealthResponse

logger = logging.getLogger(__name__)


async def _run_check(name: str, probe: Callable[[], Awaitable[Any]]) -> CheckStatus:
    """Run one probe under the health timeout and report ok or error.

    No retries on purpose: a health check must answer fast, and a monitor
    calling it repeatedly already acts as the retry.

    Args:
        name: Dependency name, used in the warning log.
        probe: Zero-argument function returning the awaitable to run.

    Returns:
        ``"ok"`` if the probe finished in time, otherwise ``"error"``.
    """
    try:
        await asyncio.wait_for(probe(), timeout=settings.health_timeout_s)
    except Exception as exc:  # any failure (timeout, refused, auth) means unhealthy
        # Log only the exception type: messages can contain connection strings.
        logger.warning("Health check failed: %s (%s)", name, type(exc).__name__)
        return "error"
    return "ok"


async def check_health(session: AsyncSession, qdrant: AsyncQdrantClient) -> HealthResponse:
    """Check Neon and Qdrant in parallel.

    Args:
        session: Database session for this request.
        qdrant: The shared Qdrant client created at startup.

    Returns:
        Overall ``"ok"`` only if every dependency is ok, with per-check results.
    """
    database, vector_db = await asyncio.gather(
        _run_check("database", lambda: session.execute(text("SELECT 1"))),
        _run_check("qdrant", qdrant.get_collections),
    )
    checks = HealthChecks(database=database, qdrant=vector_db)
    overall: CheckStatus = "ok" if database == vector_db == "ok" else "error"
    return HealthResponse(status=overall, checks=checks)
