"""Turn per-question retrieval results into summaries and Markdown reports.

Everything here is pure (no I/O, no models), so it is unit tested and the
same results always produce the same report.
"""

import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from evals.golden_set import GoldenQuestion
from evals.metrics import (
    RetrievedRef,
    hit_at_k,
    is_hit,
    mean_ignoring_none,
    recall_at_k,
    reciprocal_rank,
)

# Thresholds tried when suggesting REFUSAL_THRESHOLD (rerank scores are 0-1).
SWEEP = [round(0.05 * step, 2) for step in range(1, 13)]


@dataclass
class RetrievedRow:
    """One retrieved chunk as stored in the results file.

    Attributes:
        rank: Final position, 1 = best.
        filename: Document file name.
        page_start: First page.
        page_end: Last page.
        section: Section path joined with " > ".
        similarity: Cosine similarity to the question.
        rerank_score: Raw cross-encoder score, if reranking ran.
        score: Final sort score (boosted rerank, RRF or search score).
        exact_match: Contains an exact code from the question.
        hit: Matches one of the question's labelled sources.
    """

    rank: int
    filename: str
    page_start: int
    page_end: int
    section: str
    similarity: float
    rerank_score: float | None
    score: float
    exact_match: bool
    hit: bool


@dataclass
class QuestionResult:
    """Retrieval outcome for one golden-set question.

    Attributes:
        id: Question id.
        category: Question category.
        answerable: Whether the corpus holds the answer.
        question: The question text.
        expected: Labelled sources as "file p.N" strings.
        hit: hit@k (None for unanswerable questions).
        recall: recall@k (None for unanswerable questions).
        reciprocal_rank: 1/rank of the first hit within k (None if unanswerable).
        best_rerank_score: Best raw rerank score, if reranking ran.
        below_threshold: Retrieval would refuse with the current threshold.
        latency_ms: Total retrieval time.
        retrieved: The final chunks.
    """

    id: str
    category: str
    answerable: bool
    question: str
    expected: list[str]
    hit: float | None
    recall: float | None
    reciprocal_rank: float | None
    best_rerank_score: float | None
    below_threshold: bool
    latency_ms: float
    retrieved: list[RetrievedRow] = field(default_factory=list)


def score_question(
    question: GoldenQuestion,
    retrieved: Sequence[RetrievedRef],
    k: int,
) -> tuple[float | None, float | None, float | None, list[bool]]:
    """Compute the three metrics and per-chunk hit flags for one question.

    Args:
        question: The golden question with its source labels.
        retrieved: Retrieved chunk locations, best first.
        k: Cut-off rank.

    Returns:
        hit@k, recall@k, reciprocal rank (each None if unanswerable) and, for
        every retrieved chunk, whether it hits any labelled source.
    """
    labels = question.labels()
    flags = [any(is_hit(chunk, label) for label in labels) for chunk in retrieved]
    return (
        hit_at_k(retrieved, labels, k),
        recall_at_k(retrieved, labels, k),
        reciprocal_rank(retrieved, labels, k),
        flags,
    )


def summarise(results: Sequence[QuestionResult]) -> dict[str, object]:
    """Average the metrics overall and per category.

    Args:
        results: One result per question.

    Returns:
        ``overall`` and ``per_category`` dicts with hit_rate, recall, mrr and
        question counts, plus retrieval latency p50/p95 in milliseconds.
    """
    by_category: dict[str, list[QuestionResult]] = defaultdict(list)
    for result in results:
        by_category[result.category].append(result)
    latencies = sorted(result.latency_ms for result in results)
    return {
        "overall": _averages(results),
        "per_category": {name: _averages(group) for name, group in sorted(by_category.items())},
        "latency_ms": {"p50": _percentile(latencies, 50), "p95": _percentile(latencies, 95)},
    }


def threshold_sweep(results: Sequence[QuestionResult]) -> list[dict[str, float]]:
    """Show what each refusal threshold would do on these questions.

    A higher threshold refuses more unanswerable questions (good) but also
    more answerable ones (false refusals). Only questions with a rerank score
    count.

    Args:
        results: One result per question.

    Returns:
        Per threshold: share of answerable questions refused and share of
        unanswerable questions refused. Empty when reranking did not run.
    """
    answerable = [
        r.best_rerank_score for r in results if r.answerable and r.best_rerank_score is not None
    ]
    unanswerable = [
        r.best_rerank_score for r in results if not r.answerable and r.best_rerank_score is not None
    ]
    if not answerable and not unanswerable:
        return []
    rows = []
    for threshold in SWEEP:
        rows.append(
            {
                "threshold": threshold,
                "false_refusal_rate": _share_below(answerable, threshold),
                "unanswerable_refused_rate": _share_below(unanswerable, threshold),
            }
        )
    return rows


def config_markdown(
    name: str,
    description: str,
    split: str,
    collection: str,
    k: int,
    summary: dict[str, object],
    sweep: list[dict[str, float]],
    results: Sequence[QuestionResult],
) -> str:
    """Render one config's results as Markdown.

    Args:
        name: Config name.
        description: What the config tests.
        split: ``tune``, ``heldout`` or ``all``.
        collection: Qdrant collection searched.
        k: Metric cut-off.
        summary: Output of `summarise`.
        sweep: Output of `threshold_sweep`.
        results: Per-question results (misses are listed).

    Returns:
        The report text.
    """
    overall = summary["overall"]
    assert isinstance(overall, dict)
    per_category = summary["per_category"]
    assert isinstance(per_category, dict)
    latency = summary["latency_ms"]
    assert isinstance(latency, dict)
    lines = [
        f"# Retrieval eval: `{name}`",
        "",
        f"{description}. Split `{split}`, collection `{collection}`, k = {k}.",
        "",
        f"| Metric | Value |\n| --- | --- |\n| hit rate@{k} | {_fmt(overall['hit_rate'])} |"
        f"\n| recall@{k} | {_fmt(overall['recall'])} |\n| MRR | {_fmt(overall['mrr'])} |"
        f"\n| answerable questions | {overall['answerable']} |"
        f"\n| latency p50 / p95 (ms) | {latency['p50']:.0f} / {latency['p95']:.0f} |",
        "",
        "## Per category",
        "",
        f"| Category | n | hit@{k} | recall@{k} | MRR |",
        "| --- | --- | --- | --- | --- |",
    ]
    for category, values in per_category.items():
        lines.append(
            f"| {category} | {values['questions']} | {_fmt(values['hit_rate'])} | "
            f"{_fmt(values['recall'])} | {_fmt(values['mrr'])} |"
        )
    if sweep:
        lines += [
            "",
            "## Refusal threshold sweep (best raw rerank score)",
            "",
            "| Threshold | Answerable refused (bad) | Unanswerable refused (good) |",
            "| --- | --- | --- |",
        ]
        lines += [
            f"| {row['threshold']:.2f} | {row['false_refusal_rate']:.0%} | "
            f"{row['unanswerable_refused_rate']:.0%} |"
            for row in sweep
        ]
    misses = [r for r in results if r.answerable and r.hit == 0.0]
    lines += ["", f"## Misses (no labelled source in the top {k})", ""]
    if not misses:
        lines.append("None.")
    for miss in misses:
        top = miss.retrieved[0] if miss.retrieved else None
        got = f"{top.filename} p.{top.page_start} ({top.section})" if top else "nothing"
        lines.append(
            f"- **{miss.id}** ({miss.category}) {miss.question}  \n"
            f"  expected {', '.join(miss.expected)}; top-1 was {got}"
        )
    return "\n".join(lines) + "\n"


def comparison_markdown(
    split: str, k: int, rows: Sequence[tuple[str, str, dict[str, object]]]
) -> str:
    """Render the table comparing several configs.

    Args:
        split: The split evaluated.
        k: Metric cut-off.
        rows: (config name, description, summary) in display order.

    Returns:
        The comparison report text.
    """
    lines = [
        f"# Retrieval comparison ({split} split, k = {k})",
        "",
        f"| Config | What changes | hit@{k} | recall@{k} | MRR | p50 ms |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    categories: list[str] = []
    for name, description, summary in rows:
        overall = summary["overall"]
        latency = summary["latency_ms"]
        assert isinstance(overall, dict) and isinstance(latency, dict)
        lines.append(
            f"| `{name}` | {description} | {_fmt(overall['hit_rate'])} | "
            f"{_fmt(overall['recall'])} | {_fmt(overall['mrr'])} | {latency['p50']:.0f} |"
        )
        per_category = summary["per_category"]
        assert isinstance(per_category, dict)
        categories += [c for c in per_category if c not in categories and c != "unanswerable"]
    lines += [
        "",
        f"## hit@{k} per category",
        "",
        "| Config | " + " | ".join(categories) + " |",
        "| --- |" + " --- |" * len(categories),
    ]
    for name, _, summary in rows:
        per_category = summary["per_category"]
        assert isinstance(per_category, dict)
        cells = [_fmt(per_category.get(c, {}).get("hit_rate")) for c in categories]
        lines.append(f"| `{name}` | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _averages(results: Sequence[QuestionResult]) -> dict[str, object]:
    """Mean metrics of a group of questions (unanswerable ones are skipped)."""
    return {
        "questions": len(results),
        "answerable": sum(result.answerable for result in results),
        "hit_rate": mean_ignoring_none(result.hit for result in results),
        "recall": mean_ignoring_none(result.recall for result in results),
        "mrr": mean_ignoring_none(result.reciprocal_rank for result in results),
    }


def _percentile(sorted_values: list[float], percent: int) -> float:
    """Percentile of already sorted values (0 when empty)."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    return statistics.quantiles(sorted_values, n=100, method="inclusive")[percent - 1]


def _share_below(scores: list[float], threshold: float) -> float:
    """Share of scores under a threshold (0 when there are none)."""
    return sum(score < threshold for score in scores) / len(scores) if scores else 0.0


def _fmt(value: object) -> str:
    """Format a metric for a table: three decimals, or a dash when undefined."""
    return f"{value:.3f}" if isinstance(value, float | int) else "-"
