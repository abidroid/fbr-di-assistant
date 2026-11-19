"""Tests for /auth/signup, /auth/login and /auth/me against the Neon test branch."""

import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import User
from app.security import create_access_token
from tests.conftest import TEST_PASSWORD, signup_user


def assert_error(response: httpx.Response, status: int, code: str) -> None:
    """Check a response uses the API's single error shape.

    Args:
        response: The response to check.
        status: Expected HTTP status.
        code: Expected ``error.code``.
    """
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert error["request_id"] == response.headers["X-Request-ID"]


# --- Signup -------------------------------------------------------------------


async def test_signup_creates_user_without_password_data(db_client: httpx.AsyncClient) -> None:
    response = await db_client.post(
        "/auth/signup",
        json={"email": "dev@example.com", "full_name": "Ayesha Khan", "password": TEST_PASSWORD},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "dev@example.com"
    assert body["full_name"] == "Ayesha Khan"
    uuid.UUID(body["id"])  # raises if not a valid UUID
    assert "password" not in body
    assert "hashed_password" not in body


async def test_signup_stores_email_in_lower_case(db_client: httpx.AsyncClient) -> None:
    body = await signup_user(db_client, email="Dev.User@Example.COM")

    assert body["email"] == "dev.user@example.com"


async def test_signup_stores_password_as_argon2_hash(
    db_client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    body = await signup_user(db_client)

    async with db_sessionmaker() as session:
        user = await session.get(User, uuid.UUID(body["id"]))
    assert user is not None
    assert user.hashed_password.startswith("$argon2")
    assert TEST_PASSWORD not in user.hashed_password


async def test_signup_duplicate_email_returns_409(
    db_client: httpx.AsyncClient, registered_user: dict[str, Any]
) -> None:
    response = await db_client.post(
        "/auth/signup",
        json={"email": registered_user["email"], "full_name": "Someone", "password": TEST_PASSWORD},
    )

    assert_error(response, 409, "email_taken")


async def test_signup_duplicate_email_is_case_insensitive(
    db_client: httpx.AsyncClient, registered_user: dict[str, Any]
) -> None:
    response = await db_client.post(
        "/auth/signup",
        json={
            "email": registered_user["email"].upper(),
            "full_name": "Someone",
            "password": TEST_PASSWORD,
        },
    )

    assert_error(response, 409, "email_taken")


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"email": "not-an-email", "full_name": "A", "password": TEST_PASSWORD}, "body.email"),
        ({"email": "a@example.com", "full_name": "A", "password": "short"}, "body.password"),
        ({"email": "a@x.com", "full_name": "   ", "password": TEST_PASSWORD}, "body.full_name"),
        ({"email": "a@example.com", "full_name": "A", "password": "x" * 129}, "body.password"),
        ({"full_name": "A", "password": TEST_PASSWORD}, "body.email"),
    ],
    ids=["invalid-email", "short-password", "blank-name", "huge-password", "missing-email"],
)
async def test_signup_invalid_input_returns_422(
    db_client: httpx.AsyncClient, payload: dict[str, str], field: str
) -> None:
    response = await db_client.post("/auth/signup", json=payload)

    assert_error(response, 422, "validation_error")
    assert field in response.json()["error"]["message"]


async def test_signup_422_never_echoes_the_password(db_client: httpx.AsyncClient) -> None:
    response = await db_client.post(
        "/auth/signup",
        json={"email": "a@example.com", "full_name": "A", "password": "Secr3tX"},  # too short
    )

    assert response.status_code == 422
    assert "Secr3tX" not in response.text


# --- Login --------------------------------------------------------------------


async def test_login_returns_bearer_token(
    db_client: httpx.AsyncClient, registered_user: dict[str, Any]
) -> None:
    response = await db_client.post(
        "/auth/login", data={"username": registered_user["email"], "password": TEST_PASSWORD}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"].count(".") == 2  # header.payload.signature


async def test_login_email_is_case_insensitive(
    db_client: httpx.AsyncClient, registered_user: dict[str, Any]
) -> None:
    response = await db_client.post(
        "/auth/login",
        data={"username": registered_user["email"].upper(), "password": TEST_PASSWORD},
    )

    assert response.status_code == 200


async def test_login_wrong_password_returns_401(
    db_client: httpx.AsyncClient, registered_user: dict[str, Any]
) -> None:
    response = await db_client.post(
        "/auth/login", data={"username": registered_user["email"], "password": "wrong-password"}
    )

    assert_error(response, 401, "invalid_credentials")
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_login_unknown_email_gives_same_error_as_wrong_password(
    db_client: httpx.AsyncClient, registered_user: dict[str, Any]
) -> None:
    wrong_password = await db_client.post(
        "/auth/login", data={"username": registered_user["email"], "password": "wrong-password"}
    )
    unknown_email = await db_client.post(
        "/auth/login", data={"username": "nobody@example.com", "password": TEST_PASSWORD}
    )

    assert_error(unknown_email, 401, "invalid_credentials")
    assert unknown_email.json()["error"]["message"] == wrong_password.json()["error"]["message"]


async def test_login_missing_fields_returns_422(db_client: httpx.AsyncClient) -> None:
    response = await db_client.post("/auth/login", data={"username": "dev@example.com"})

    assert_error(response, 422, "validation_error")


# --- /auth/me (CurrentUser) ---------------------------------------------------


async def test_me_returns_current_user(
    db_client: httpx.AsyncClient, registered_user: dict[str, Any], auth_headers: dict[str, str]
) -> None:
    response = await db_client.get("/auth/me", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == registered_user


async def test_me_without_token_returns_401(db_client: httpx.AsyncClient) -> None:
    response = await db_client.get("/auth/me")

    assert_error(response, 401, "invalid_credentials")
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_me_with_garbage_token_returns_401(db_client: httpx.AsyncClient) -> None:
    response = await db_client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})

    assert_error(response, 401, "invalid_credentials")


async def test_me_with_expired_token_returns_401(
    db_client: httpx.AsyncClient, registered_user: dict[str, Any]
) -> None:
    expired = create_access_token(registered_user["id"], expires_delta=timedelta(seconds=-1))

    response = await db_client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})

    assert_error(response, 401, "invalid_credentials")


async def test_me_with_non_uuid_subject_returns_401(db_client: httpx.AsyncClient) -> None:
    token = create_access_token("not-a-uuid")

    response = await db_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert_error(response, 401, "invalid_credentials")


async def test_me_for_deleted_user_returns_401(
    db_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    registered_user: dict[str, Any],
    auth_headers: dict[str, str],
) -> None:
    # The token is still validly signed, but its user is gone.
    async with db_sessionmaker() as session:
        await session.execute(delete(User).where(User.id == uuid.UUID(registered_user["id"])))
        await session.commit()

    response = await db_client.get("/auth/me", headers=auth_headers)

    assert_error(response, 401, "invalid_credentials")
