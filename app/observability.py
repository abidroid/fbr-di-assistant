"""Logging setup and request-id middleware (one log line per request).

Every request gets an id: the caller's ``X-Request-ID`` if it looks safe,
otherwise a new random one. The id is returned in the ``X-Request-ID`` response
header and in every error body, and is attached to every log line written while
the request runs, so one user complaint can be traced to the exact log lines.
Langfuse tracing is added in a later phase.
"""

import logging
import re
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"
# Accept a caller's id only if it is short and plain, so it cannot inject fake
# lines or huge strings into our logs.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")

# Holds the current request's id for code that has no access to `request`.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

logger = logging.getLogger("app.request")


class RequestIdFilter(logging.Filter):
    """Add ``record.request_id`` to every log record so the format can print it."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Attach the current request id unless the record already has one.

        Args:
            record: The log record being emitted.

        Returns:
            Always True: the filter only enriches records, it never drops them.
        """
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        return True


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging with the request id in every line.

    Args:
        level: Log level name, e.g. ``"INFO"`` or ``"DEBUG"``.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s")
    )
    handler.addFilter(RequestIdFilter())
    # force=True replaces handlers from earlier calls (tests, reloads) instead of
    # stacking them, which would print every line twice.
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)
    # Our middleware writes the per-request line; uvicorn's access log would
    # duplicate it without the request id or latency.
    logging.getLogger("uvicorn.access").disabled = True


def new_request_id(incoming: str | None) -> str:
    """Reuse a safe incoming request id or create a new one.

    Args:
        incoming: Value of the caller's ``X-Request-ID`` header, if any.

    Returns:
        The id to use for this request.
    """
    if incoming and _SAFE_REQUEST_ID.match(incoming):
        return incoming
    return uuid.uuid4().hex


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Give each request an id, time it, and log one summary line."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Run the request with an id and log method, path, status and latency.

        Args:
            request: The incoming request.
            call_next: Passes the request on to the rest of the app.

        Returns:
            The app's response, with the ``X-Request-ID`` header added.
        """
        request_id = new_request_id(request.headers.get(REQUEST_ID_HEADER))
        # request.state is shared with the exception handlers, which need the id
        # even after this middleware's context has ended (unhandled 500s).
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        # If the app raises, no response comes back; the 500 handler answers it.
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        finally:
            latency_ms = (time.perf_counter() - start) * 1000
            # user_id is set by the auth dependency (Phase 2); "-" when anonymous.
            user_id = getattr(request.state, "user_id", None) or "-"
            logger.info(
                "method=%s path=%s status=%d latency_ms=%.1f user_id=%s",
                request.method,
                request.url.path,
                status_code,
                latency_ms,
                user_id,
            )
            request_id_var.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
