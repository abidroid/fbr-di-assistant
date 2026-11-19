"""Tests for GET /health, the shared error shape and request-id handling."""

import httpx
from fastapi import FastAPI
from qdrant_client import AsyncQdrantClient

from app.exceptions import DocumentNotFoundError
from tests.conftest import FakeSession


async def test_health_ok_when_all_dependencies_respond(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "qdrant": "ok"}}


async def test_health_503_when_database_fails(
    client: httpx.AsyncClient, fake_session: FakeSession
) -> None:
    fake_session.fail = True

    response = await client.get("/health")

    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "error", "qdrant": "ok"}


async def test_health_503_when_qdrant_fails(
    client: httpx.AsyncClient, qdrant: AsyncQdrantClient
) -> None:
    await qdrant.close()  # a closed client raises on every call

    response = await client.get("/health")

    assert response.status_code == 503
    assert response.json()["checks"]["qdrant"] == "error"


async def test_unknown_route_returns_error_shape(client: httpx.AsyncClient) -> None:
    response = await client.get("/does-not-exist")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"] == response.headers["X-Request-ID"]


async def test_wrong_method_returns_405_in_error_shape(client: httpx.AsyncClient) -> None:
    response = await client.post("/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


async def test_safe_incoming_request_id_is_reused(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "trace-abc-123"})

    assert response.headers["X-Request-ID"] == "trace-abc-123"


async def test_unsafe_incoming_request_id_is_replaced(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "bad id\twith spaces"})

    request_id = response.headers["X-Request-ID"]
    assert request_id != "bad id\twith spaces"
    assert len(request_id) == 32  # uuid4 hex


async def test_domain_error_maps_to_its_status(app: FastAPI, client: httpx.AsyncClient) -> None:
    @app.get("/_test/domain")
    async def raise_domain_error() -> None:
        raise DocumentNotFoundError()

    response = await client.get("/_test/domain")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "document_not_found"


async def test_validation_error_returns_422_in_error_shape(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    @app.get("/_test/validate")
    async def needs_int(n: int) -> int:
        return n

    response = await client.get("/_test/validate", params={"n": "not-a-number"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert "query.n" in error["message"]


async def test_unhandled_error_returns_generic_500(app: FastAPI, client: httpx.AsyncClient) -> None:
    @app.get("/_test/crash")
    async def crash() -> None:
        raise RuntimeError("internal detail that must not leak")

    response = await client.get("/_test/crash")

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert "internal detail" not in error["message"]
    assert error["request_id"] == response.headers["X-Request-ID"]
