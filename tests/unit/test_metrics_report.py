"""Unit tests for the monitoring report's maths (no Langfuse needed)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from scripts.metrics_report import (
    Observation,
    format_markdown,
    group_traces,
    percentile,
    select_traces,
    summarise,
    to_observation,
)

T0 = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)


def obs(trace: str, name: str, ms: float, *, minute: int = 0, **extra: object) -> Observation:
    """Build an observation; the root is the one named ``ask``."""
    return Observation(
        trace_id=trace,
        name=name,
        is_root=name == "ask",
        start=T0 + timedelta(minutes=minute),
        latency_ms=ms,
        **extra,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("values", "p", "expected"),
    [
        ([], 50, 0.0),
        ([7.0], 95, 7.0),
        ([1, 2, 3, 4], 50, 2),
        ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50, 5),
        ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95, 10),
        (list(range(1, 101)), 95, 95),
    ],
)
def test_percentile_is_nearest_rank(values: list[float], p: float, expected: float) -> None:
    assert percentile(values, p) == expected


def test_report_has_latency_tokens_cost_and_error_rate() -> None:
    observations = [
        obs("a", "ask", 10_000, tags=("api",)),
        obs("a", "rerank", 6_000),
        obs("a", "llm.ModelAnswer", 3_000, input_tokens=2000, output_tokens=200, cost=0.006),
        obs("b", "ask", 20_000, minute=1, tags=("api",)),
        obs("b", "rerank", 8_000),
        obs("b", "llm.ModelAnswer", 9_000, input_tokens=4000, output_tokens=400, cost=0.012),
        obs("b", "search", 50, level="ERROR"),
    ]
    traces = select_traces(group_traces(observations), "api", last=10)
    report = summarise(traces)

    assert report.traces == 2
    assert report.total.p50_ms == 10_000
    assert report.total.p95_ms == 20_000
    assert report.input_tokens == 3000
    assert report.cost_mean == pytest.approx(0.009)
    assert report.error_rate == 0.5
    # Slowest step (by p50) first.
    assert [s.name for s in report.steps][:2] == ["rerank", "llm.ModelAnswer"]
    llm = next(s for s in report.steps if s.name == "llm.ModelAnswer")
    assert llm.output_tokens == 300
    assert "Error rate: **50.0%**" in format_markdown(report, "api")


def test_select_keeps_newest_traces_with_the_tag() -> None:
    observations = [
        obs("old", "ask", 1, minute=0, tags=("api",)),
        obs("new", "ask", 1, minute=5, tags=("api",)),
        obs("eval", "ask", 1, minute=9, tags=("eval", "config:final")),
        obs("orphan", "rerank", 1),  # root not fetched: incomplete, skipped
    ]
    traces = group_traces(observations)
    assert [t.trace_id for t in select_traces(traces, "api", last=1)] == ["new"]
    assert [t.trace_id for t in select_traces(traces, "config:final", 5)] == ["eval"]
    assert len(select_traces(traces, None, 10)) == 3


def test_warning_does_not_count_as_an_error() -> None:
    traces = group_traces([obs("a", "ask", 5, level="WARNING", tags=("api",))])
    assert summarise(list(traces.values())).error_rate == 0.0


def test_api_row_is_converted_with_cached_tokens_counted_as_input() -> None:
    row = SimpleNamespace(
        trace_id="t",
        name="llm.ModelAnswer",
        parent_observation_id="p",
        is_root_observation=False,
        start_time=T0,
        end_time=T0 + timedelta(milliseconds=1500),
        level=SimpleNamespace(value="DEFAULT"),
        model="claude-sonnet-5-5",
        usage_details={"input": 100, "output": 20, "cache_read_input_tokens": 50},
        total_cost=None,
        cost_details={"total": 0.0012},
        tags=["api"],
    )
    result = to_observation(row)
    assert result.latency_ms == pytest.approx(1500)
    assert result.input_tokens == 150
    assert result.cost == pytest.approx(0.0012)
    assert result.is_root is False


def test_root_is_detected_even_with_a_remote_parent_id() -> None:
    # Setting the trace id ourselves gives the root a (hidden) remote parent.
    row = SimpleNamespace(
        trace_id="t",
        name="ask",
        parent_observation_id="remote-parent",
        is_root_observation=True,
        start_time=T0,
        end_time=None,
        level=None,
        model=None,
        usage_details=None,
        total_cost=None,
        cost_details=None,
        tags=["api"],
    )
    result = to_observation(row)
    assert result.is_root is True
    assert result.latency_ms is None
    assert result.level == "DEFAULT"
