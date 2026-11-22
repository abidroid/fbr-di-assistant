"""FastAPI application factory: middleware, routers, exception handlers, lifespan.

Request flow: RequestIdMiddleware -> CORS -> router -> route -> service. Any
exception on the way is turned into ``{"error": {code, message, request_id}}``
by the handlers registered here, the only place that maps errors to statuses.
"""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus

import anyio
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from qdrant_client import AsyncQdrantClient
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import InterfaceError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.db import engine
from app.exceptions import DomainError, UpstreamUnavailableError
from app.generation import ClaudeLLM, leak_markers
from app.guardrails import LLMClassifier, limiter, redact
from app.ingestion.caption import ClaudeCaptioner
from app.ingestion.embed import create_embedders
from app.ingestion.extract import Extractor
from app.ingestion.store import ensure_collection
from app.observability import (
    REQUEST_ID_HEADER,
    RequestIdMiddleware,
    setup_logging,
    setup_tracing,
    shutdown_tracing,
)
from app.retrieval import create_reranker
from app.routes import ask, auth, documents, health
from app.schemas import ErrorDetail, ErrorResponse

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run startup work once before serving and cleanup once at shutdown.

    Args:
        app: The application being started.

    Yields:
        Control back to FastAPI while the app serves requests.
    """
    # One client for the whole app: it keeps a connection pool, so creating one
    # per request would waste a connection setup on every call.
    api_key = settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None
    app.state.qdrant = AsyncQdrantClient(
        url=settings.qdrant_url, api_key=api_key, timeout=settings.qdrant_timeout_s
    )
    # Heavy models load once here, not per request: BGE-M3 takes ~25 s and
    # ~2 GB of RAM. Loading runs in a thread so startup logs keep flowing.
    logger.info("Loading embedding models (BGE-M3 dense, BM25 sparse)...")
    app.state.dense_embedder, app.state.sparse_embedder = await anyio.to_thread.run_sync(
        create_embedders
    )
    logger.info("Loading reranker (bge-reranker-v2-m3)...")
    app.state.reranker = await anyio.to_thread.run_sync(create_reranker)
    app.state.extractor = Extractor(ClaudeCaptioner())
    # One Anthropic client for every /ask request (it pools connections).
    app.state.llm = ClaudeLLM()
    # The input guardrail shares that client; it only asks a different model.
    app.state.classifier = LLMClassifier(app.state.llm)
    try:
        await ensure_collection(app.state.qdrant)
    except Exception:
        # Qdrant may still be starting; /health reports it, and the first
        # upload creates the collection anyway.
        logger.warning("Could not prepare the Qdrant collection at startup", exc_info=True)
    # Langfuse if its keys are in .env, otherwise a no-op (tests never get here).
    setup_tracing(redact=lambda text: redact(text, leak_markers()))
    logger.info("Startup complete")
    try:
        yield
    finally:
        # Spans are sent in background batches; flush so the last ones are not lost.
        shutdown_tracing()
        await app.state.qdrant.close()
        # Close pooled Neon connections cleanly instead of letting them be dropped.
        await engine.dispose()


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build a response in the API's single error shape.

    Args:
        request: The failed request; supplies the request id.
        status_code: HTTP status to return.
        code: Machine-readable error name.
        message: Human-readable explanation.
        headers: Extra headers, e.g. ``WWW-Authenticate``.

    Returns:
        A JSON response with the ``X-Request-ID`` header set.
    """
    request_id = getattr(request.state, "request_id", "-")
    body = ErrorResponse(error=ErrorDetail(code=code, message=message, request_id=request_id))
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(),
        headers={**(headers or {}), REQUEST_ID_HEADER: request_id},
    )


async def domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map any `DomainError` raised by a service to its declared status.

    Args:
        request: The failed request.
        exc: The raised exception (always a `DomainError` given how it is registered).

    Returns:
        The standard error response.
    """
    # Starlette types handlers as taking any Exception; this narrows the type.
    assert isinstance(exc, DomainError)
    return error_response(request, exc.status_code, exc.code, exc.message, exc.headers)


async def http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Give framework errors (unknown route 404, wrong method 405) our shape.

    Args:
        request: The failed request.
        exc: A Starlette `HTTPException`.

    Returns:
        The standard error response, e.g. code ``not_found``.
    """
    assert isinstance(exc, StarletteHTTPException)
    phrase = HTTPStatus(exc.status_code).phrase
    code = phrase.lower().replace(" ", "_").replace("-", "_")
    message = exc.detail if isinstance(exc.detail, str) else phrase
    return error_response(request, exc.status_code, code, message, exc.headers)


async def rate_limit_handler(request: Request, exc: Exception) -> JSONResponse:
    """Answer 429 in our error shape, with ``Retry-After`` in seconds.

    Args:
        request: The refused request.
        exc: slowapi's `RateLimitExceeded`.

    Returns:
        A 429 error response.
    """
    assert isinstance(exc, RateLimitExceeded)
    return error_response(
        request,
        429,
        "rate_limited",
        f"Too many questions: the limit is {exc.detail}. Please wait and try again.",
        headers={"Retry-After": str(_retry_after_seconds(request, exc))},
    )


def _retry_after_seconds(request: Request, exc: RateLimitExceeded) -> int:
    """Seconds until the user's window resets.

    Args:
        request: The refused request; slowapi stored the limit and key on it.
        exc: The rate-limit error.

    Returns:
        Seconds to wait (at least 1). Falls back to the window length if the
        counter cannot be read.
    """
    try:
        limit, keys = request.state.view_rate_limit
        reset_at, _remaining = limiter.limiter.get_window_stats(limit, *keys)
        return max(1, int(reset_at - time.time()) + 1)
    except Exception:
        return int(exc.limit.limit.get_expiry())


async def database_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Turn a lost database connection in the middle of a request into a 503.

    `get_db` already retries opening the connection; this catches a failure
    later in the request (Neon suspended, network dropped), which is not the
    client's fault and may work again shortly, so 503 rather than 500.

    Args:
        request: The failed request.
        exc: SQLAlchemy's `OperationalError` or `InterfaceError`.

    Returns:
        The standard 503 error response.
    """
    logger.warning("Database unavailable during the request", exc_info=exc)
    error = UpstreamUnavailableError()
    return error_response(request, error.status_code, error.code, error.message)


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Turn request validation failures into a 422 in our shape.

    Only field locations and messages are reported, never the submitted values,
    which could include passwords.

    Args:
        request: The failed request.
        exc: FastAPI's `RequestValidationError`.

    Returns:
        A 422 error response such as ``"body.question: Field required"``.
    """
    assert isinstance(exc, RequestValidationError)
    problems = [
        f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
    ]
    return error_response(request, 422, "validation_error", "; ".join(problems[:5]))


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch anything unexpected: log the full trace, return a generic 500.

    Args:
        request: The failed request.
        exc: Any exception no other handler caught.

    Returns:
        A 500 error response that reveals no internal details.
    """
    request_id = getattr(request.state, "request_id", "-")
    logger.exception("Unhandled error", exc_info=exc, extra={"request_id": request_id})
    return error_response(request, 500, "internal_error", "An unexpected error occurred.")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        The configured app. Tests call this to get a fresh instance.
    """
    setup_logging(settings.log_level)
    app = FastAPI(
        title="FBR Digital Invoicing Assistant",
        version="0.1.0",
        description=(
            "Ask questions about your FBR Digital Invoicing documents and get answers "
            "grounded in them, with citations, or a clear refusal.\n\n"
            'Every error returns `{"error": {"code", "message", "request_id"}}`.'
        ),
        lifespan=lifespan,
    )
    # The React app runs on another origin (port 5173), so the browser needs CORS.
    # Auth uses bearer tokens, not cookies, so credentials are not needed.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )
    # Added last, so it is the outermost layer and also times CORS preflights.
    app.add_middleware(RequestIdMiddleware)

    # slowapi reads the limiter from app.state.
    app.state.limiter = limiter
    app.add_exception_handler(DomainError, domain_error_handler)
    # RateLimitExceeded subclasses HTTPException; Starlette picks the most
    # specific handler, so this one wins over the generic HTTP handler below.
    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
    app.add_exception_handler(OperationalError, database_error_handler)
    app.add_exception_handler(InterfaceError, database_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(documents.router)
    app.include_router(ask.router)
    return app


app = create_app()
