"""Shared pytest fixtures.

Two kinds of app fixture:
- ``client``: fake database session, for tests that need no tables (/health).
- ``db_client``: the real Neon ``test`` branch (TEST_DATABASE_URL), tables
  created once per run and emptied before every test, for auth and later
  document tests. Qdrant always runs in memory.
"""

import os

# Set BEFORE importing the app: settings are read at import time. Environment
# variables beat .env, so tests always use dummy secrets and can never touch the
# dev database or call the real Anthropic API.
os.environ["DATABASE_URL"] = "postgresql://test:test@localhost:5432/unused"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["ANTHROPIC_API_KEY"] = "test-key-not-real"

from collections.abc import AsyncIterator  # noqa: E402
from typing import Any  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from qdrant_client import AsyncQdrantClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app import models  # noqa: E402, F401  # registers the tables on Base.metadata
from app.config import settings  # noqa: E402
from app.db import Base, create_db_engine, get_db  # noqa: E402
from app.deps import get_qdrant  # noqa: E402
from app.main import create_app  # noqa: E402

TEST_PASSWORD = "a-long-secret-phrase"


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


# --- Real database (Neon test branch) -----------------------------------------


@pytest.fixture(scope="session")
async def db_engine() -> AsyncIterator[AsyncEngine]:
    """Connect to the Neon ``test`` branch and create the tables once per run.

    NullPool opens a fresh connection for each use and closes it afterwards,
    so no connection outlives the test that opened it.
    """
    if settings.test_database_url is None:
        pytest.skip("TEST_DATABASE_URL is not set; skipping tests that need Neon.")
    engine = create_db_engine(settings.test_database_url.get_secret_value(), poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_sessionmaker(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Empty every table, then return a session factory bound to the test database.

    Truncating BEFORE each test (not after) means a crashed test can never
    leave rows behind that break the next one.
    """
    async with db_engine.begin() as conn:
        await conn.execute(text("TRUNCATE users, documents RESTART IDENTITY CASCADE"))
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def db_client(
    db_sessionmaker: async_sessionmaker[AsyncSession], qdrant: AsyncQdrantClient
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an HTTP client for an app that uses the real test database."""
    application = create_app()

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        # One session per request, exactly like the production get_db().
        async with db_sessionmaker() as session:
            yield session

    application.dependency_overrides[get_db] = override_get_db
    application.dependency_overrides[get_qdrant] = lambda: qdrant
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def signup_user(
    client: httpx.AsyncClient, email: str = "dev@example.com", full_name: str = "Test Developer"
) -> dict[str, Any]:
    """Create an account through the API.

    Args:
        client: The test HTTP client.
        email: Email for the new account.
        full_name: Display name for the new account.

    Returns:
        The signup response body (the new user).
    """
    response = await client.post(
        "/auth/signup", json={"email": email, "full_name": full_name, "password": TEST_PASSWORD}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def login_headers(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    """Log in through the API and build the Authorization header.

    Args:
        client: The test HTTP client.
        email: Email of an existing account (password is ``TEST_PASSWORD``).

    Returns:
        ``{"Authorization": "Bearer <token>"}``.
    """
    response = await client.post("/auth/login", data={"username": email, "password": TEST_PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def registered_user(db_client: httpx.AsyncClient) -> dict[str, Any]:
    """Create one account and return it (password is ``TEST_PASSWORD``)."""
    return await signup_user(db_client)


@pytest.fixture
async def auth_headers(
    db_client: httpx.AsyncClient, registered_user: dict[str, Any]
) -> dict[str, str]:
    """Return a valid Authorization header for ``registered_user``."""
    return await login_headers(db_client, registered_user["email"])
