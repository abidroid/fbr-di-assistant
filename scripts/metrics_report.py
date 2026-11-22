"""Monitoring report from Langfuse: latency per step, tokens, cost and error rate.

Pulls the observations (spans and generations) of the last N traces from
Langfuse, groups them by trace and prints:

- p50 / p95 latency of every step (guardrails, embed, search, rerank, LLM calls),
- input / output tokens and USD cost per question, per LLM call type,
- error rate: share of traces with at least one ``ERROR`` span (a blocked
  injection is ``WARNING`` and does not count).

Usage::

    uv run python -m scripts.metrics_report --last 50 --tag api
    uv run python -m scripts.metrics_report --last 30 --tag config:final --markdown

The maths lives in pure functions (`percentile`, `summarise`) that the unit
tests check without a network or a Langfuse account.
"""

import argparse
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import settings

RESULTS_DIR = Path("evals/results")
# Field groups to request: ids/times (core), name/level (basic), tokens and
# cost (usage), model id (model) and tags (trace_context).
FIELDS = "core,basic,usage,model,trace_context"


@dataclass(frozen=True)
class Observation:
    """One span or generation, reduced to what the report needs.

    Attributes:
        trace_id: The trace it belongs to.
        name: Step name, e.g. ``"rerank"`` or ``"llm.ModelAnswer"``.
        is_root: True for the trace's root span (``ask`` / ``eval``).
        start: Start time.
        latency_ms: Duration in milliseconds (None if it never ended).
        level: ``DEFAULT``, ``WARNING`` or ``ERROR``.
        model: Model id (generations only).
        input_tokens: Input tokens, cache reads and writes included.
        output_tokens: Output tokens.
        cost: USD cost.
        tags: Tags of the trace.
    """

    trace_id: str
    name: str
    is_root: bool
    start: datetime
    latency_ms: float | None
    level: str = "DEFAULT"
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    tags: tuple[str, ...] = ()


@dataclass
class TraceSummary:
    """All observations of one trace (one question).

    Attributes:
        trace_id: The trace id.
        root: The root span, or None if it was not fetched.
        steps: Every non-root observation.
    """

    trace_id: str
    root: Observation | None = None
    steps: list[Observation] = field(default_factory=list)

    @property
    def has_error(self) -> bool:
        """True if any span of the trace is ``ERROR``."""
        observations = self.steps + ([self.root] if self.root else [])
        return any(o.level == "ERROR" for o in observations)


@dataclass(frozen=True)
class StepStats:
    """Latency (and, for LLM calls, tokens and cost) of one step name.

    Attributes:
        name: Step name.
        count: How many times it ran.
        p50_ms: Median latency.
        p95_ms: 95th-percentile latency.
        input_tokens: Mean input tokens per call.
        output_tokens: Mean output tokens per call.
        cost: Mean USD cost per call.
    """

    name: str
    count: int
    p50_ms: float
    p95_ms: float
    input_tokens: float = 0.0
    output_tokens: float = 0.0
    cost: float = 0.0


@dataclass(frozen=True)
class Report:
    """The whole report.

    Attributes:
        traces: Number of traces (questions) included.
        total: Latency of the whole question (the root span).
        steps: Per-step statistics, slowest p50 first.
        input_tokens: Mean input tokens per question.
        output_tokens: Mean output tokens per question.
        cost_mean: Mean USD cost per question.
        cost_p95: 95th-percentile USD cost per question.
        error_rate: Share of traces with an ERROR span (0-1).
    """

    traces: int
    total: StepStats
    steps: list[StepStats]
    input_tokens: float
    output_tokens: float
    cost_mean: float
    cost_p95: float
    error_rate: float


# --- Pure functions (unit tested) ----------------------------------------------------


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile: the smallest value with ``p``% of values at or below it.

    Nearest-rank always returns a real measurement (no interpolation), which
    is easy to explain and honest with the small samples we have (20-50).

    Args:
        values: The measurements.
        p: Percentile, 0-100.

    Returns:
        The percentile, or 0.0 for an empty list.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def group_traces(observations: Iterable[Observation]) -> dict[str, TraceSummary]:
    """Group observations by trace.

    Args:
        observations: Observations of any number of traces (duplicates allowed).

    Returns:
        Traces by id.
    """
    traces: dict[str, TraceSummary] = {}
    for obs in observations:
        summary = traces.setdefault(obs.trace_id, TraceSummary(obs.trace_id))
        if obs.is_root:
            summary.root = obs
        else:
            summary.steps.append(obs)
    return traces


def select_traces(
    traces: dict[str, TraceSummary], tag: str | None, last: int
) -> list[TraceSummary]:
    """Keep the newest ``last`` complete traces that carry ``tag``.

    Args:
        traces: Traces by id.
        tag: Required tag (e.g. ``"api"``, ``"config:final"``), or None for any.
        last: How many to keep.

    Returns:
        Newest first.
    """
    complete = [t for t in traces.values() if t.root is not None]
    if tag:
        complete = [t for t in complete if tag in t.root.tags]  # type: ignore[union-attr]
    complete.sort(key=lambda t: t.root.start, reverse=True)  # type: ignore[union-attr]
    return complete[:last]


def step_stats(name: str, observations: Sequence[Observation]) -> StepStats:
    """Statistics of one step over all its runs.

    Args:
        name: The step name.
        observations: Every run of that step.

    Returns:
        Count, p50/p95 latency and mean tokens and cost.
    """
    latencies = [o.latency_ms for o in observations if o.latency_ms is not None]
    count = len(observations)
    return StepStats(
        name=name,
        count=count,
        p50_ms=percentile(latencies, 50),
        p95_ms=percentile(latencies, 95),
        input_tokens=sum(o.input_tokens for o in observations) / count if count else 0.0,
        output_tokens=sum(o.output_tokens for o in observations) / count if count else 0.0,
        cost=sum(o.cost for o in observations) / count if count else 0.0,
    )


def summarise(traces: Sequence[TraceSummary]) -> Report:
    """Build the report from the selected traces.

    Args:
        traces: Complete traces (each has a root).

    Returns:
        The report.
    """
    by_name: dict[str, list[Observation]] = defaultdict(list)
    for summary in traces:
        for obs in summary.steps:
            by_name[obs.name].append(obs)
    steps = sorted(
        (step_stats(name, runs) for name, runs in by_name.items()),
        key=lambda s: s.p50_ms,
        reverse=True,
    )
    roots = [t.root for t in traces if t.root is not None]
    costs = [sum(o.cost for o in t.steps) for t in traces]
    count = len(traces)
    return Report(
        traces=count,
        total=step_stats("total (whole question)", roots),
        steps=steps,
        input_tokens=sum(sum(o.input_tokens for o in t.steps) for t in traces) / count
        if count
        else 0.0,
        output_tokens=sum(sum(o.output_tokens for o in t.steps) for t in traces) / count
        if count
        else 0.0,
        cost_mean=sum(costs) / count if count else 0.0,
        cost_p95=percentile(costs, 95),
        error_rate=sum(t.has_error for t in traces) / count if count else 0.0,
    )


def format_markdown(report: Report, tag: str | None) -> str:
    """Render the report as Markdown (printed, and saved with ``--markdown``).

    Args:
        report: The report.
        tag: The tag filter used (shown in the heading).

    Returns:
        Markdown text.
    """
    lines = [
        f"# Monitoring report ({date.today().isoformat()}, tag: {tag or 'any'})",
        "",
        f"- Questions (traces): **{report.traces}**",
        f"- Latency per question: p50 **{report.total.p50_ms / 1000:.1f} s**, "
        f"p95 **{report.total.p95_ms / 1000:.1f} s**",
        f"- Tokens per question: {report.input_tokens:,.0f} in / {report.output_tokens:,.0f} out",
        f"- Cost per question: mean **${report.cost_mean:.4f}**, p95 ${report.cost_p95:.4f}",
        f"- Error rate: **{report.error_rate:.1%}**",
        "",
        "| Step | Runs | p50 ms | p95 ms | Tokens in | Tokens out | Cost/call $ |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for s in [report.total, *report.steps]:
        tokens_in = f"{s.input_tokens:,.0f}" if s.input_tokens else ""
        tokens_out = f"{s.output_tokens:,.0f}" if s.output_tokens else ""
        cost = f"{s.cost:.5f}" if s.cost else ""
        lines.append(
            f"| {s.name} | {s.count} | {s.p50_ms:,.0f} | {s.p95_ms:,.0f} | "
            f"{tokens_in} | {tokens_out} | {cost} |"
        )
    return "\n".join(lines) + "\n"


def to_observation(row: Any) -> Observation:
    """Convert one Langfuse API row into an `Observation`.

    Args:
        row: An ``ObservationV2`` from the Langfuse API.

    Returns:
        The reduced observation.
    """
    usage = row.usage_details or {}
    latency = None
    if row.end_time is not None:
        latency = (row.end_time - row.start_time).total_seconds() * 1000
    cost = row.total_cost
    if cost is None:
        cost = (row.cost_details or {}).get("total", 0.0)
    return Observation(
        trace_id=row.trace_id,
        name=row.name or "?",
        is_root=row.parent_observation_id is None,
        start=row.start_time,
        latency_ms=latency,
        level=str(getattr(row.level, "value", row.level) or "DEFAULT"),
        model=row.model,
        input_tokens=usage.get("input", 0)
        + usage.get("cache_read_input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0),
        output_tokens=usage.get("output", 0),
        cost=float(cost or 0.0),
        tags=tuple(row.tags or ()),
    )


# --- Fetching and the command ------------------------------------------------------


def fetch_observations(days: int, max_items: int) -> list[Observation]:
    """Download recent observations from Langfuse.

    Args:
        days: How far back to look.
        max_items: Upper bound on rows downloaded.

    Returns:
        The observations (deduplicated by id).

    Raises:
        SystemExit: If the Langfuse keys are not configured.
    """
    from langfuse import Langfuse  # imported here: the pure functions need no SDK

    if settings.langfuse_public_key is None or settings.langfuse_secret_key is None:
        raise SystemExit("Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY in .env first.")
    client = Langfuse(
        public_key=settings.langfuse_public_key.get_secret_value(),
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        base_url=settings.langfuse_host,
        # This client only reads; it must not open a span exporter of its own.
        tracing_enabled=False,
    )
    seen: dict[str, Observation] = {}
    pages = client.iter_observation_pages(
        fields=FIELDS,
        from_start_time=datetime.now(UTC) - timedelta(days=days),
        max_items=max_items,
        limit=1000,
    )
    for page in pages:
        for row in page.data:
            # The API may return a row twice across pages; keep one.
            seen[row.id] = to_observation(row)
    return list(seen.values())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Read the command-line options.

    Args:
        argv: Arguments (None = ``sys.argv``).

    Returns:
        The parsed options.
    """
    parser = argparse.ArgumentParser(description="Latency, tokens, cost and errors from Langfuse.")
    parser.add_argument("--last", type=int, default=50, help="number of traces (questions)")
    parser.add_argument("--tag", default="api", help='"api", "eval", "config:final", or "" for any')
    parser.add_argument("--days", type=int, default=7, help="how far back to search")
    parser.add_argument("--max-items", type=int, default=20000, help="cap on rows downloaded")
    parser.add_argument("--markdown", action="store_true", help="also save to evals/results/")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Fetch, summarise and print (and optionally save) the report.

    Args:
        argv: Arguments (None = ``sys.argv``).
    """
    args = parse_args(argv)
    tag = args.tag or None
    observations = fetch_observations(args.days, args.max_items)
    traces = select_traces(group_traces(observations), tag, args.last)
    if not traces:
        raise SystemExit(f"No traces with tag {tag!r} in the last {args.days} days.")
    text = format_markdown(summarise(traces), tag)
    print(text)
    if args.markdown:
        safe_tag = (tag or "any").replace(":", "-")
        path = RESULTS_DIR / f"{date.today().isoformat()}_metrics_{safe_tag}.md"
        path.write_text(text, encoding="utf-8")
        print(f"Saved {path}")


if __name__ == "__main__":
    main()
