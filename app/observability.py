"""Logging, request ids and Langfuse tracing.

Logging: every request gets an id (the caller's ``X-Request-ID`` if it looks
safe, otherwise a new random one). The id is returned in the ``X-Request-ID``
header and in every error body, and is attached to every log line, so one user
complaint leads to the exact log lines.

Tracing: one question becomes one *trace* (a tree of timed steps) made of
*spans* (guardrails, embedding, search, rerank, generation) and *generations*
(LLM calls with model, tokens and cost). Pipeline code only calls `trace()` and
`span()`; a swappable backend decides what happens to them:

- `NoopTracer` (default): nothing is recorded or sent. Used when the Langfuse
  keys are absent, so tests and fresh clones never need an account.
- `LangfuseTracer`: sends to Langfuse Cloud in a background thread.
- `RecordingTracer`: keeps spans in a list so tests can inspect them.

We choose explicitly what each span carries (ids, scores, verdicts, the answer)
instead of using Langfuse's ``@observe`` decorator, which would send every
argument, including whole document chunks and prompts with the canary.
"""

import hashlib
import logging
import re
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal

from langfuse import Langfuse, propagate_attributes
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings
from app.exceptions import DomainError

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


# --- Tracing ---------------------------------------------------------------------

Level = Literal["DEFAULT", "WARNING", "ERROR"]
Kind = Literal["trace", "span", "generation"]

tracing_logger = logging.getLogger("app.tracing")


@dataclass
class SpanRecord:
    """What one span collected; backends turn it into their own format.

    Attributes:
        name: Step name, e.g. ``"rerank"``.
        kind: ``trace`` (the root), ``span`` (a step) or ``generation`` (an LLM call).
        input: What the step received (kept small: no document text).
        output: What the step produced.
        metadata: Extra facts, e.g. ``{"candidates": 30}``.
        level: ``DEFAULT``, ``WARNING`` (a client mistake) or ``ERROR`` (a failure).
        status_message: Short reason for a non-default level.
        model: Model id (generations only).
        usage: Token counts (generations only).
        cost: USD cost by token type (generations only).
    """

    name: str
    kind: Kind
    input: Any = None
    output: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    level: Level = "DEFAULT"
    status_message: str | None = None
    model: str | None = None
    usage: dict[str, int] | None = None
    cost: dict[str, float] | None = None


@dataclass(frozen=True)
class TraceInfo:
    """Attributes that belong to a whole trace, not to one step.

    Attributes:
        request_id: The request id; the Langfuse trace id is derived from it.
        user_id: Hashed user id (never the email or raw id), or None.
        tags: Labels to filter by, e.g. ``("api",)`` or ``("eval", "config:final")``.
    """

    request_id: str
    user_id: str | None
    tags: tuple[str, ...]


class Span:
    """Handle the pipeline holds inside ``with span(...) as step:``.

    It updates the local record (for tests) and, if a backend observation
    exists, forwards the same update to it.
    """

    def __init__(self, record: SpanRecord, backend: Any) -> None:
        """Wrap a record and the backend's observation.

        Args:
            record: The local record of this span.
            backend: The backend's observation object, or None (no-op/recording).
        """
        self.record = record
        self._backend = backend

    def update(
        self,
        *,
        output: Any = None,
        level: Level | None = None,
        status_message: str | None = None,
        model: str | None = None,
        usage: Mapping[str, int] | None = None,
        cost: Mapping[str, float] | None = None,
        **metadata: Any,
    ) -> None:
        """Add results to the span; only the fields passed are changed.

        Args:
            output: What the step produced.
            level: New level (``WARNING`` / ``ERROR``).
            status_message: Short reason for the level.
            model: Model that actually served an LLM call.
            usage: Token counts.
            cost: USD cost by token type.
            **metadata: Extra facts to attach.
        """
        changes: dict[str, Any] = {}
        if output is not None:
            self.record.output = changes["output"] = output
        if level is not None:
            self.record.level = changes["level"] = level
        if status_message is not None:
            self.record.status_message = changes["status_message"] = status_message
        if model is not None:
            self.record.model = changes["model"] = model
        if usage is not None:
            self.record.usage = changes["usage_details"] = dict(usage)
        if cost is not None:
            self.record.cost = changes["cost_details"] = dict(cost)
        if metadata:
            self.record.metadata.update(metadata)
            changes["metadata"] = metadata
        if self._backend is not None and changes:
            self._backend.update(**changes)


class NoopTracer:
    """Tracing off: spans run the code inside them and record nothing.

    It is also the interface the other backends implement.
    """

    @contextmanager
    def open(self, record: SpanRecord, trace: TraceInfo | None) -> Iterator[Any]:
        """Open a span (does nothing).

        Args:
            record: The span being opened.
            trace: Trace attributes if this is the root span, else None.

        Yields:
            None: there is no backend observation.
        """
        yield None

    def shutdown(self) -> None:
        """Nothing to flush."""


class RecordingTracer(NoopTracer):
    """Test backend: keeps every span and trace in memory.

    Attributes:
        records: Spans in the order they were opened.
        traces: Trace attributes of every root span.
    """

    def __init__(self) -> None:
        """Start with empty lists."""
        self.records: list[SpanRecord] = []
        self.traces: list[TraceInfo] = []

    @contextmanager
    def open(self, record: SpanRecord, trace: TraceInfo | None) -> Iterator[Any]:
        """Remember the span (and trace attributes) without sending anything.

        Args:
            record: The span being opened.
            trace: Trace attributes if this is the root span, else None.

        Yields:
            None: updates go to the record only.
        """
        self.records.append(record)
        if trace is not None:
            self.traces.append(trace)
        yield None

    def names(self) -> list[str]:
        """Return the names of all recorded spans, in order."""
        return [record.name for record in self.records]

    def get(self, name: str) -> SpanRecord:
        """Return the first recorded span with this name.

        Raises:
            KeyError: If no span has that name.
        """
        for record in self.records:
            if record.name == name:
                return record
        raise KeyError(name)


class LangfuseTracer(NoopTracer):
    """Real backend: Langfuse Cloud (OpenTelemetry spans, sent in batches)."""

    def __init__(self, client: Langfuse) -> None:
        """Wrap a Langfuse client.

        Args:
            client: The configured client (created once in `setup_tracing`).
        """
        self._client = client

    @contextmanager
    def open(self, record: SpanRecord, trace: TraceInfo | None) -> Iterator[Any]:
        """Open a Langfuse observation as the current span.

        Nesting is automatic: Langfuse keeps the current span in a context
        variable, so a span opened inside another becomes its child, also
        across ``await`` and ``asyncio.gather``.

        Args:
            record: The span being opened.
            trace: Trace attributes if this is the root span, else None.

        Yields:
            The Langfuse observation, which receives `Span.update` calls.
        """
        options: dict[str, Any] = {
            "name": record.name,
            "as_type": "generation" if record.kind == "generation" else "span",
            "input": record.input,
            "metadata": record.metadata or None,
            "model": record.model,
        }
        if trace is None:
            with self._client.start_as_current_observation(**options) as observation:
                yield observation
            return
        # Same request id -> same trace id, so the id in an error body finds the trace.
        trace_id = Langfuse.create_trace_id(seed=trace.request_id)
        with self._client.start_as_current_observation(
            trace_context={"trace_id": trace_id}, **options
        ) as observation:
            # Applies user, tags and request id to this trace and every child span.
            with propagate_attributes(
                user_id=trace.user_id,
                tags=list(trace.tags),
                metadata={"request_id": trace.request_id},
                trace_name=record.name,
            ):
                yield observation

    def shutdown(self) -> None:
        """Send any spans still queued (at app shutdown and after an eval run)."""
        self._client.shutdown()


_tracer: NoopTracer = NoopTracer()


def mask_strings(data: Any, redact: Callable[[str], str]) -> Any:
    """Apply a redaction function to every string inside nested data.

    Args:
        data: A span's input, output or metadata (str, dict, list or scalar).
        redact: Turns one string into its redacted form.

    Returns:
        The same structure with every string redacted.
    """
    if isinstance(data, str):
        return redact(data)
    if isinstance(data, Mapping):
        return {key: mask_strings(value, redact) for key, value in data.items()}
    if isinstance(data, list | tuple):
        return [mask_strings(value, redact) for value in data]
    return data


def setup_tracing(redact: Callable[[str], str] | None = None) -> NoopTracer:
    """Use Langfuse if both keys are configured, otherwise stay a no-op.

    Args:
        redact: Applied to every string sent to Langfuse (the app passes the
            guardrails' leak redaction, so a leaked canary or key never
            reaches a third party even if a model repeats it).

    Returns:
        The tracer now in use.
    """
    public_key, secret_key = settings.langfuse_public_key, settings.langfuse_secret_key
    if public_key is None or secret_key is None:
        tracing_logger.info("Langfuse keys not set; tracing is off")
        set_tracer(NoopTracer())
        return _tracer
    client = Langfuse(
        public_key=public_key.get_secret_value(),
        secret_key=secret_key.get_secret_value(),
        base_url=settings.langfuse_host,
        environment=settings.langfuse_environment,
        mask=(lambda *, data, **_: mask_strings(data, redact)) if redact else None,
    )
    set_tracer(LangfuseTracer(client))
    tracing_logger.info("Langfuse tracing on (environment=%s)", settings.langfuse_environment)
    return _tracer


def set_tracer(tracer: NoopTracer) -> NoopTracer:
    """Replace the active tracer (setup and tests).

    Args:
        tracer: The new backend.

    Returns:
        The previous backend, so a test can put it back.
    """
    global _tracer
    previous, _tracer = _tracer, tracer
    return previous


def shutdown_tracing() -> None:
    """Flush queued spans; safe to call when tracing is off."""
    _tracer.shutdown()


def hash_user_id(user_id: uuid.UUID | str) -> str:
    """Turn a user id into a stable pseudonym for traces.

    Stable, so Langfuse can still group one user's questions; one-way, so
    no internal id (and never an email) reaches a third-party service.

    Args:
        user_id: The user's id.

    Returns:
        The first 16 hex characters of its SHA-256.
    """
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:16]


def llm_cost(model: str, usage: Mapping[str, int]) -> dict[str, float]:
    """Price one LLM call from its token counts.

    Args:
        model: Model id that served the call.
        usage: Token counts with keys ``input``, ``output``,
            ``cache_read_input_tokens`` and ``cache_creation_input_tokens``.

    Returns:
        USD by token type plus ``total``; empty if the model has no price
        (Langfuse then shows tokens without cost).
    """
    prices = settings.model_prices.get(model)
    if prices is None:
        tracing_logger.warning("No price configured for model %s; cost not recorded", model)
        return {}
    per_million = 1_000_000
    cost = {
        "input": usage.get("input", 0) * prices["input"] / per_million,
        "output": usage.get("output", 0) * prices["output"] / per_million,
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0)
        * prices["cache_read"]
        / per_million,
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0)
        * prices["cache_write"]
        / per_million,
    }
    cost["total"] = sum(cost.values())
    return cost


def error_level(exc: BaseException) -> Level:
    """Pick the level for a span whose code raised.

    Client mistakes (4xx domain errors, e.g. a blocked injection) are
    ``WARNING``; outages (503) and bugs are ``ERROR``. The metrics report's
    error rate counts ``ERROR`` only, so attackers cannot inflate it.

    Args:
        exc: The exception.

    Returns:
        The level.
    """
    if isinstance(exc, DomainError) and exc.status_code < 500:
        return "WARNING"
    return "ERROR"


@contextmanager
def _observe(record: SpanRecord, info: TraceInfo | None) -> Iterator[Span]:
    """Open a span with the active backend and mark it if the code inside raises.

    Args:
        record: The span to open.
        info: Trace attributes for a root span, else None.

    Yields:
        The span handle.
    """
    with _tracer.open(record, info) as backend:
        handle = Span(record, backend)
        try:
            yield handle
        except Exception as exc:
            # Class name only: a message may contain user text or upstream details.
            handle.update(level=error_level(exc), status_message=type(exc).__name__)
            raise


@contextmanager
def trace(
    name: str,
    *,
    request_id: str,
    user_id: uuid.UUID | str | None = None,
    tags: Iterable[str] = (),
    input: Any = None,
) -> Iterator[Span]:
    """Open the root span of one trace (one question).

    Args:
        name: Trace name, e.g. ``"ask"`` or ``"eval"``.
        request_id: The request id (the trace id is derived from it).
        user_id: The user's id; it is hashed before it leaves this function.
        tags: Labels to filter traces by.
        input: What the trace received (the question).

    Yields:
        The root span handle.
    """
    info = TraceInfo(
        request_id=request_id,
        user_id=hash_user_id(user_id) if user_id is not None else None,
        tags=tuple(tags),
    )
    record = SpanRecord(name, "trace", input=input, metadata={"request_id": request_id})
    with _observe(record, info) as root:
        yield root


@contextmanager
def span(
    name: str,
    *,
    kind: Literal["span", "generation"] = "span",
    input: Any = None,
    model: str | None = None,
    **metadata: Any,
) -> Iterator[Span]:
    """Open one step inside the current trace.

    Args:
        name: Step name, e.g. ``"rerank"``.
        kind: ``span`` for a step, ``generation`` for an LLM call.
        input: What the step received (keep it small).
        model: Model id (generations only).
        **metadata: Extra facts known when the step starts.

    Yields:
        The span handle.
    """
    record = SpanRecord(name, kind, input=input, model=model, metadata=dict(metadata))
    with _observe(record, None) as step:
        yield step
