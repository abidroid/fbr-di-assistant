"""Shared pytest fixtures.

Phase 1 tests need no real database: the session is a fake and Qdrant runs
in memory. Phase 2 adds the Neon ``test`` branch for tests that need tables.
"""

import os

# Set BEFORE importing the app: settings are read at import time. Environment
# variables beat .env, so tests always use dummy secrets and can never touch the
# dev database or call the real Anthropic API.
os.environ["DATABASE_URL"] = "postgresql://test:test@localhost:5432/unused"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["ANTHROPIC_API_KEY"] = "test-key-not-real"

from collections.abc import AsyncIterator  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from qdrant_client import AsyncQdrantClient  # noqa: E402

from app.db import get_db  # noqa: E402
from app.deps import get_qdrant  # noqa: E402
from app.main import create_app  # noqa: E402


class FakeSession:
    """Stands in for `AsyncSession` where a test only needs ``execute`` to work.

    Attributes:
        fail: When True, ``execute`` raises, simulating Neon being down.
    """

    def __init__(self, fail: bool = False) -> None:
        """Create the fake.

        Args:
            fail: Whether every ``execute`` call should raise.
        """
        self.fail = fail

    async def execute(self, *args: object, **kwargs: object) -> None:
        """Pretend to run a statement.

        Args:
            *args: Ignored.
            **kwargs: Ignored.

        Raises:
            ConnectionError: If the fake was created with ``fail=True``.
        """
        if self.fail:
            raise ConnectionError("database down")


@pytest.fixture
def fake_session() -> FakeSession:
    """Return a working fake session; tests set ``.fail = True`` to break it."""
    return FakeSession()


@pytest.fixture
async def qdrant() -> AsyncIterator[AsyncQdrantClient]:
    """Yield an in-memory Qdrant client, closed after the test."""
    client = AsyncQdrantClient(location=":memory:")
    yield client
    await client.close()


@pytest.fixture
def app(fake_session: FakeSession, qdrant: AsyncQdrantClient) -> FastAPI:
    """Build a fresh app whose database and Qdrant dependencies are fakes."""
    application = create_app()
    application.dependency_overrides[get_db] = lambda: fake_session
    application.dependency_overrides[get_qdrant] = lambda: qdrant
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an HTTP client that calls the app in-process (no real server)."""
    # raise_app_exceptions=False lets an unhandled error come back as our 500
    # response instead of crashing the test.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
