"""Summaries and the Markdown report for the full (answer) evaluation stage.

Kept apart from `evals/report.py` (retrieval only) so each file stays small.
Everything here is pure: the same results always give the same report.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from evals.metrics import mean_ignoring_none
from evals.report import QuestionResult, RetrievedRow, _fmt

# Thresholds tried for REFUSAL_THRESHOLD on the real answer outcomes.
FULL_SWEEP = [0.0, 0.03, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3]


@dataclass
class AnswerResult:
    """Everything the full stage records for one question.

    Attributes:
        retrieval: The question's retrieval metrics (same as ``--stage retrieval``).
        reference: Golden-set reference answer.
        standalone_question: The question after the follow-up rewrite.
        answer: What the system answered.
        refused: The system refused (any reason).
        refusal_reason: ``below_threshold``, ``model``, ``no_citation``, ... or None.
        citations: Cited sources as "file p.N" strings.
        faithfulness: Supported claims / claims (None if refused).
        correctness: Factual F1 against the reference (None if unanswerable).
        verdict: correct / partly / wrong.
        verdict_reason: The judge's (or the rule's) reason.
        citations_supported: Every citation backs its sentence (None if refused).
        unsupported_claims: Claims not supported by the retrieved chunks.
        generation_ms: Time in the answering model.
        total_ms: Time for the whole pipeline.
    """

    retrieval: QuestionResult
    reference: str
    standalone_question: str
    answer: str
    refused: bool
    refusal_reason: str | None
    citations: list[str]
    faithfulness: float | None
    correctness: float | None
    verdict: str
    verdict_reason: str
    citations_supported: bool | None
    unsupported_claims: list[str] = field(default_factory=list)
    generation_ms: float = 0.0
    total_ms: float = 0.0


def answer_result_from_dict(data: dict[str, object]) -> AnswerResult:
    """Rebuild an `AnswerResult` saved with ``dataclasses.asdict`` (checkpoint files).

    Args:
        data: The saved dict.

    Returns:
        The result, with its nested retrieval result and rows restored.
    """
    fields = dict(data)
    retrieval = dict(fields.pop("retrieval"))  # type: ignore[call-overload]
    rows = [RetrievedRow(**row) for row in retrieval.pop("retrieved")]
    return AnswerResult(retrieval=QuestionResult(**retrieval, retrieved=rows), **fields)  # type: ignore[arg-type]


def summarise_answers(results: Sequence[AnswerResult]) -> dict[str, object]:
    """Average the answer metrics overall and per category.

    Args:
        results: One result per question.

    Returns:
        ``overall`` and ``per_category`` dicts.
    """
    by_category: dict[str, list[AnswerResult]] = defaultdict(list)
    for result in results:
        by_category[result.retrieval.category].append(result)
    return {
        "overall": _answer_averages(results),
        "per_category": {
            name: _answer_averages(group) for name, group in sorted(by_category.items())
        },
    }


def _answer_averages(results: Sequence[AnswerResult]) -> dict[str, object]:
    """Answer metrics of a group of questions.

    Correctness counts a false refusal as 0, so refusing too often shows up
    as lower correctness. Faithfulness only covers answers that were given.
    """
    answerable = [r for r in results if r.retrieval.answerable]
    unanswerable = [r for r in results if not r.retrieval.answerable]
    answered = [r for r in results if not r.refused]
    verdicts = Counter(r.verdict for r in results)
    return {
        "questions": len(results),
        "faithfulness": mean_ignoring_none(r.faithfulness for r in answered),
        "correctness": mean_ignoring_none(r.correctness for r in answerable),
        "correct": verdicts.get("correct", 0),
        "partly": verdicts.get("partly", 0),
        "wrong": verdicts.get("wrong", 0),
        "accuracy": verdicts.get("correct", 0) / len(results) if results else None,
        "citation_support": mean_ignoring_none(
            None if r.citations_supported is None else float(r.citations_supported)
            for r in answered
        ),
        "false_refusal_rate": _share(r.refused for r in answerable),
        "unanswerable_refused_rate": _share(r.refused for r in unanswerable),
    }


def answer_threshold_sweep(results: Sequence[AnswerResult]) -> list[dict[str, float]]:
    """Replay the refusal decision for other thresholds on the real outcomes.

    Raising the threshold only adds refusals: a question refused in the run
    stays refused, and one answered becomes refused if its best rerank score
    is under t. Only thresholds at or above the one used in the run can be
    replayed: below it, the refused questions never reached the model.

    Args:
        results: One result per question.

    Returns:
        Per threshold: false refusal rate, unanswerable refused rate, and the
        share of answerable questions answered correctly.
    """
    rows = []
    for threshold in FULL_SWEEP:
        refused = {r.retrieval.id: r.refused or _score(r) < threshold for r in results}
        answerable = [r for r in results if r.retrieval.answerable]
        unanswerable = [r for r in results if not r.retrieval.answerable]
        rows.append(
            {
                "threshold": threshold,
                "false_refusal_rate": _share(refused[r.retrieval.id] for r in answerable),
                "unanswerable_refused_rate": _share(refused[r.retrieval.id] for r in unanswerable),
                "answerable_correct_rate": _share(
                    not refused[r.retrieval.id] and r.verdict == "correct" for r in answerable
                ),
            }
        )
    return rows


def answers_markdown(
    name: str,
    split: str,
    judge_model: str,
    summary: dict[str, object],
    sweep: list[dict[str, float]],
    results: Sequence[AnswerResult],
    threshold: float,
) -> str:
    """Render the full-stage report as Markdown.

    Args:
        name: Config name.
        split: The split evaluated.
        judge_model: Model that graded the answers.
        summary: Output of `summarise_answers`.
        sweep: Output of `answer_threshold_sweep`.
        results: Per-question results (problems are listed).
        threshold: REFUSAL_THRESHOLD used in the run.

    Returns:
        The report text.
    """
    overall = summary["overall"]
    per_category = summary["per_category"]
    assert isinstance(overall, dict) and isinstance(per_category, dict)
    lines = [
        f"# Answer eval: `{name}`",
        "",
        f"Split `{split}`, {overall['questions']} questions, judge `{judge_model}`, "
        f"refusal threshold {threshold}.",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| faithfulness (answered) | {_fmt(overall['faithfulness'])} |",
        f"| answer correctness (factual F1, answerable) | {_fmt(overall['correctness'])} |",
        f"| judge: correct / partly / wrong | {overall['correct']} / {overall['partly']} / "
        f"{overall['wrong']} |",
        f"| accuracy (verdict correct) | {_fmt(overall['accuracy'])} |",
        f"| citation support (answered) | {_fmt(overall['citation_support'])} |",
        f"| unanswerable correctly refused | {_pct(overall['unanswerable_refused_rate'])} |",
        f"| false refusals (answerable refused) | {_pct(overall['false_refusal_rate'])} |",
        "",
        "## Per category",
        "",
        "| Category | n | faithfulness | correctness | correct | partly | wrong |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for category, values in per_category.items():
        lines.append(
            f"| {category} | {values['questions']} | {_fmt(values['faithfulness'])} | "
            f"{_fmt(values['correctness'])} | {values['correct']} | {values['partly']} | "
            f"{values['wrong']} |"
        )
    lines += [
        "",
        "## Refusal threshold sweep (real answer outcomes)",
        "",
        "| Threshold | Answerable refused (bad) | Unanswerable refused (good) | "
        "Answerable correct |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        f"| {row['threshold']:.2f} | {_pct(row['false_refusal_rate'])} | "
        f"{_pct(row['unanswerable_refused_rate'])} | {_pct(row['answerable_correct_rate'])} |"
        for row in sweep
        if row["threshold"] >= threshold
    ]
    problems = [r for r in results if r.verdict != "correct"]
    lines += ["", "## Not correct", ""]
    if not problems:
        lines.append("None.")
    for result in problems:
        q = result.retrieval
        lines.append(
            f"- **{q.id}** ({q.category}, {result.verdict}) {q.question}  \n"
            f"  answer: {_one_line(result.answer)}  \n"
            f"  reason: {result.verdict_reason}"
        )
    return "\n".join(lines) + "\n"


def _score(result: AnswerResult) -> float:
    """Best raw rerank score of a question (0 when there is none)."""
    return result.retrieval.best_rerank_score or 0.0


def _share(flags: Iterable[bool]) -> float | None:
    """Share of True values (None when there are none)."""
    values = list(flags)
    return sum(values) / len(values) if values else None


def _pct(value: object) -> str:
    """Format a share as a percentage, or a dash when undefined."""
    return f"{value:.0%}" if isinstance(value, float | int) else "-"


def _one_line(text: str, limit: int = 200) -> str:
    """Collapse an answer to one short line for the report."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
