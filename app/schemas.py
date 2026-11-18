"""Pydantic request and response models (the API's public contract)."""

from http import HTTPStatus
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


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
