"""Unit tests for tracing: backends, levels, user hashing, cost and redaction."""

import uuid
from typing import Any

import pytest

from app import observability
from app.config import settings
from app.exceptions import GuardrailBlockedError, UpstreamUnavailableError
from app.generation import leak_markers, usage_details
from app.guardrails import redact
from app.observability import (
    NoopTracer,
    RecordingTracer,
    hash_user_id,
    llm_cost,
    mask_strings,
    setup_tracing,
    span,
    trace,
)


def test_tracing_is_off_without_langfuse_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "langfuse_public_key", None)
    monkeypatch.setattr(settings, "langfuse_secret_key", None)
    tracer = setup_tracing()
    assert type(tracer) is NoopTracer
    # Spans still run the code inside them.
    with trace("ask", request_id="r1") as root, span("rerank") as step:
        step.update(output="fine")
    assert root.record.name == "ask"


def test_spans_nest_inside_a_trace_with_hashed_user(tracer: RecordingTracer) -> None:
    user = uuid.uuid4()
    with trace("ask", request_id="req-1", user_id=user, tags=["api"]):
        with span("retrieval"), span("rerank") as step:
            step.update(best_rerank_score=0.7)
    assert tracer.names() == ["ask", "retrieval", "rerank"]
    assert tracer.get("rerank").metadata == {"best_rerank_score": 0.7}
    [info] = tracer.traces
    assert info.request_id == "req-1"
    assert info.tags == ("api",)
    assert info.user_id == hash_user_id(user)
    assert str(user) not in info.user_id


def test_failure_marks_span_error_and_still_raises(tracer: RecordingTracer) -> None:
    with pytest.raises(UpstreamUnavailableError):
        with trace("ask", request_id="r"), span("generation"):
            raise UpstreamUnavailableError()
    assert tracer.get("generation").level == "ERROR"
    assert tracer.get("generation").status_message == "UpstreamUnavailableError"
    assert tracer.get("ask").level == "ERROR"


def test_client_mistake_is_a_warning_not_an_error(tracer: RecordingTracer) -> None:
    with pytest.raises(GuardrailBlockedError), trace("ask", request_id="r"):
        raise GuardrailBlockedError("blocked")
    assert tracer.get("ask").level == "WARNING"


def test_user_hash_is_stable_and_short() -> None:
    user = uuid.uuid4()
    assert hash_user_id(user) == hash_user_id(str(user))
    assert len(hash_user_id(user)) == 16
    assert hash_user_id(user) != hash_user_id(uuid.uuid4())


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        # 1,000 in + 500 out at $2 / $10 per million tokens.
        ("claude-sonnet-5-5", 1000 * 2 / 1e6 + 500 * 10 / 1e6),
        ("claude-haiku-5-5", 1000 * 0.1 / 1e6 + 500 * 0.5 / 1e6),
        ("claude-opus-5-5", 1000 * 4 / 1e6 + 500 * 20 / 1e6),
    ],
)
def test_llm_cost_uses_the_price_table(model: str, expected: float) -> None:
    cost = llm_cost(model, {"input": 1000, "output": 500})
    assert cost["total"] == pytest.approx(expected)
    assert cost["input"] + cost["output"] == pytest.approx(expected)


def test_cached_tokens_are_priced_separately() -> None:
    cost = llm_cost("claude-sonnet-5-5", {"cache_read_input_tokens": 1_000_000})
    assert cost["total"] == pytest.approx(0.10)


def test_unknown_model_has_no_cost() -> None:
    assert llm_cost("some-other-model", {"input": 10, "output": 10}) == {}


class _Usage:
    """Stand-in for the SDK's `Usage` object."""

    input_tokens = 1200
    output_tokens = 300
    cache_read_input_tokens = None
    cache_creation_input_tokens = None


def test_usage_details_reads_sdk_usage() -> None:
    assert usage_details(_Usage()) == {
        "input": 1200,
        "output": 300,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }


def test_redaction_removes_canary_and_secrets_from_nested_data() -> None:
    data: dict[str, Any] = {
        "answer": f"marker {settings.prompt_canary} here",
        "items": ["key sk-ant-api03-abcdefghijklmnop", 3],
    }
    masked = mask_strings(data, lambda text: redact(text, leak_markers()))
    flat = str(masked)
    assert settings.prompt_canary not in flat
    assert "sk-ant-" not in flat
    assert masked["items"][1] == 3


def test_set_tracer_returns_the_previous_backend() -> None:
    first = RecordingTracer()
    previous = observability.set_tracer(first)
    assert observability.set_tracer(previous) is first
