"""FastAPI application factory: middleware, routers, exception handlers, lifespan.

Request flow: RequestIdMiddleware -> CORS -> router -> route -> service. Any
exception on the way is turned into ``{"error": {code, message, request_id}}``
by the handlers registered here, the only place that maps errors to statuses.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.db import engine
from app.exceptions import DomainError
from app.observability import REQUEST_ID_HEADER, RequestIdMiddleware, setup_logging
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
    yield
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

    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    return app


app = create_app()
