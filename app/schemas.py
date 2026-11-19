"""Pydantic request and response models (the API's public contract)."""

import uuid
from datetime import datetime
from http import HTTPStatus
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, StringConstraints


class ErrorDetail(BaseModel):
    """The inside of every error response.

    Attributes:
        code: Stable machine-readable error name.
        message: Human-readable explanation.
        request_id: Matches the ``X-Request-ID`` header and the server log line.
    """

    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    """The one error shape used by every endpoint, including 422 and 500."""

    error: ErrorDetail

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "error": {
                        "code": "document_not_found",
                        "message": "Document not found.",
                        "request_id": "3f2b9c0e8a7d4e61b1c2d3e4f5a6b7c8",
                    }
                }
            ]
        }
    )


CheckStatus = Literal["ok", "error"]


class HealthChecks(BaseModel):
    """Result of each dependency check.

    Attributes:
        database: Neon answered ``SELECT 1`` in time.
        qdrant: Qdrant listed its collections in time.
    """

    database: CheckStatus
    qdrant: CheckStatus


class HealthResponse(BaseModel):
    """Body of GET /health: overall status plus each dependency's status."""

    status: CheckStatus
    checks: HealthChecks

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"status": "ok", "checks": {"database": "ok", "qdrant": "ok"}},
                {"status": "error", "checks": {"database": "ok", "qdrant": "error"}},
            ]
        }
    )


# --- Auth ---------------------------------------------------------------------

# Emails are case-insensitive in practice, so store one canonical form; otherwise
# "Ali@x.com" and "ali@x.com" could become two accounts.
NormalizedEmail = Annotated[EmailStr, AfterValidator(str.lower)]
FullName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class UserCreate(BaseModel):
    """Body of POST /auth/signup.

    Attributes:
        email: Login name; must be a valid address, stored in lower case.
        full_name: Display name, 1-200 characters after trimming spaces.
        password: 8-128 characters. The upper limit stops someone sending a
            huge string to make the (deliberately slow) argon2 hash even slower.
    """

    email: NormalizedEmail
    full_name: FullName
    password: str = Field(min_length=8, max_length=128)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "email": "developer@example.com",
                    "full_name": "Ayesha Khan",
                    "password": "a-long-secret-phrase",
                }
            ]
        }
    )


class UserRead(BaseModel):
    """A user as returned by the API; never includes the password hash.

    Attributes:
        id: The user's UUID.
        email: Login email (lower case).
        full_name: Display name.
        created_at: When the account was created.
    """

    id: uuid.UUID
    email: str
    full_name: str
    created_at: datetime

    # from_attributes=True lets FastAPI build this straight from a `User` ORM row.
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "0b6f3c5e-2a4d-4c1e-9f7a-8d2e1b3c4a5f",
                    "email": "developer@example.com",
                    "full_name": "Ayesha Khan",
                    "created_at": "2026-11-19T10:15:00Z",
                }
            ]
        },
    )


class Token(BaseModel):
    """Body of a successful POST /auth/login (the OAuth2 token response).

    Attributes:
        access_token: The signed JWT to send as ``Authorization: Bearer <token>``.
        token_type: Always ``bearer``; the OAuth2 spec and Swagger expect this field.
    """

    access_token: str
    token_type: Literal["bearer"] = "bearer"

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    "token_type": "bearer",
                }
            ]
        }
    )


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """Build a route's ``responses=`` entry so Swagger shows its error codes.

    Args:
        *status_codes: The error statuses the route can return, e.g. 401, 404.

    Returns:
        A mapping of status code to the shared `ErrorResponse` model.
    """
    return {
        code: {"model": ErrorResponse, "description": HTTPStatus(code).phrase}
        for code in status_codes
    }
