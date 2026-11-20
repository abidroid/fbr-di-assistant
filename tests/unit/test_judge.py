"""Unit tests for evals.judge and evals.answer_report (no real model)."""

import pytest

from app.config import settings
from app.generation import FakeLLM
from evals.answer_report import AnswerResult, answer_threshold_sweep, summarise_answers
from evals.judge import (
    ClaimCheck,
    ClaimChecks,
    Claims,
    FactComparison,
    Verdict,
    cited_texts,
    factual_f1,
    faithfulness_score,
    score_answer,
    strip_for_grading,
)
from evals.judge_check import agreement, cohen_kappa, select_sample
from evals.report import QuestionResult


def test_faithfulness_is_the_share_of_supported_claims() -> None:
    checks = [ClaimCheck(claim="a", supported=True), ClaimCheck(claim="b", supported=False)]
    assert faithfulness_score(checks) == 0.5
    assert faithfulness_score([]) is None


@pytest.mark.parametrize(
    ("tp", "fp", "fn", "expected"),
    [(2, 0, 0, 1.0), (1, 1, 0, 2 / 3), (1, 0, 1, 2 / 3), (0, 2, 1, 0.0), (0, 0, 0, 0.0)],
)
def test_factual_f1(tp: int, fp: int, fn: int, expected: float) -> None:
    assert factual_f1(tp, fp, fn) == pytest.approx(expected)


def test_disclaimer_is_not_graded() -> None:
    assert strip_for_grading(f"Thirty days [1].\n\n{settings.legal_disclaimer}") == (
        "Thirty days [1]."
    )


def test_cited_texts_pairs_markers_with_chunks() -> None:
    assert cited_texts("A [2]. B [1][2]. C [9].", ["one", "two"]) == [(1, "one"), (2, "two")]


async def test_refusals_are_scored_by_rule_without_a_model_call() -> None:
    llm = FakeLLM()
    common = {"question": "q", "reference": "r", "answer": "x", "contexts": [], "cited": []}
    good = await score_answer(expected_answerable=False, refused=True, llm=llm, **common)
    bad = await score_answer(expected_answerable=True, refused=True, llm=llm, **common)
    assert (good.verdict, good.correctness) == ("correct", None)
    assert (bad.verdict, bad.correctness) == ("wrong", 0.0)
    assert llm.calls == []


async def test_answer_gets_every_measurement() -> None:
    llm = FakeLLM(
        [
            Claims(claims=["The extension is up to 30 days."]),
            FactComparison(true_positives=["30 days"], false_positives=[], false_negatives=[]),
            Verdict(verdict="correct", reason="Matches.", citations_supported=True),
            ClaimChecks(
                checks=[ClaimCheck(claim="The extension is up to 30 days.", supported=True)]
            ),
        ]
    )
    score = await score_answer(
        question="How long?",
        expected_answerable=True,
        reference="Up to thirty days.",
        answer="Up to 30 days [1].",
        refused=False,
        contexts=["... not exceeding thirty days."],
        cited=[(1, "... not exceeding thirty days.")],
        llm=llm,
    )
    assert score.faithfulness == 1.0
    assert score.correctness == 1.0
    assert score.verdict == "correct"
    assert all(call.model == settings.judge_model for call in llm.calls)


def make_result(
    qid: str, answerable: bool, refused: bool, best: float, verdict: str
) -> AnswerResult:
    """Build an answer result with just the fields the summaries read."""
    retrieval = QuestionResult(
        id=qid,
        category="legal_rule" if answerable else "unanswerable",
        answerable=answerable,
        question="q",
        expected=[],
        hit=None,
        recall=None,
        reciprocal_rank=None,
        best_rerank_score=best,
        below_threshold=False,
        latency_ms=1.0,
    )
    return AnswerResult(
        retrieval=retrieval,
        reference="r",
        standalone_question="q",
        answer="a",
        refused=refused,
        refusal_reason="model" if refused else None,
        citations=[],
        faithfulness=None if refused else 1.0,
        correctness=(0.0 if refused else 1.0) if answerable else None,
        verdict=verdict,
        verdict_reason="",
        citations_supported=None if refused else True,
    )


RESULTS = [
    make_result("q1", True, False, 0.9, "correct"),
    make_result("q2", True, False, 0.08, "correct"),
    make_result("q3", True, True, 0.5, "wrong"),
    make_result("q4", False, True, 0.02, "correct"),
    make_result("q5", False, False, 0.12, "wrong"),
]


def test_summary_counts_refusals_both_ways() -> None:
    overall = summarise_answers(RESULTS)["overall"]
    assert isinstance(overall, dict)
    assert overall["false_refusal_rate"] == pytest.approx(1 / 3)
    assert overall["unanswerable_refused_rate"] == 0.5
    assert overall["correct"] == 3 and overall["wrong"] == 2
    # The false refusal counts as 0 correctness.
    assert overall["correctness"] == pytest.approx(2 / 3)


def test_raising_the_threshold_only_adds_refusals() -> None:
    rows = {row["threshold"]: row for row in answer_threshold_sweep(RESULTS)}
    assert rows[0.05]["false_refusal_rate"] == pytest.approx(1 / 3)
    assert rows[0.1]["false_refusal_rate"] == pytest.approx(2 / 3)  # q2 now refused
    assert rows[0.15]["unanswerable_refused_rate"] == 1.0  # q5 now refused too
    assert rows[0.1]["answerable_correct_rate"] == pytest.approx(1 / 3)


# --- judge_check ---------------------------------------------------------------


def test_agreement_and_kappa() -> None:
    perfect = [("correct", "correct"), ("wrong", "wrong")]
    assert agreement(perfect) == 1.0 and cohen_kappa(perfect) == 1.0
    # Agreeing 3 of 4 times, but mostly on the label both use the most.
    pairs = [("correct", "correct")] * 3 + [("wrong", "correct")]
    assert agreement(pairs) == 0.75
    assert cohen_kappa(pairs) == pytest.approx(0.0)


def test_sample_covers_every_verdict_and_is_reproducible() -> None:
    questions = [
        {"verdict": verdict, "retrieval": {"id": f"q{n:03}", "category": "legal_rule"}}
        for n, verdict in enumerate(["correct"] * 30 + ["partly"] * 3 + ["wrong"] * 2)
    ]
    sample = select_sample(questions, size=10)
    assert len(sample) == 10
    assert {q["verdict"] for q in sample} == {"correct", "partly", "wrong"}
    assert sample == select_sample(questions, size=10)
