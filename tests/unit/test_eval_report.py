"""Tests for evals/report.py and the pure helpers of evals/ingest_corpus.py."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from app.ingestion.extract import ExtractedBlock
from evals.golden_set import GoldenQuestion
from evals.ingest_corpus import _block_from_json, corpus_documents
from evals.metrics import RetrievedRef
from evals.report import (
    QuestionResult,
    comparison_markdown,
    config_markdown,
    score_question,
    summarise,
    threshold_sweep,
)


def make_question(answerable: bool = True, category: str = "legal_rule") -> GoldenQuestion:
    """A golden question whose answer is on page 91 of rules.pdf (if answerable)."""
    return GoldenQuestion(
        id="q001",
        question="How long can the date be extended?",
        category="unanswerable" if not answerable else category,  # type: ignore[arg-type]
        answerable=answerable,
        sources=[{"doc": "rules.pdf", "pages": [91]}] if answerable else [],  # type: ignore[list-item]
        reference_answer="Sixty days.",
        split="tune",
    )


def make_result(
    category: str = "legal_rule",
    answerable: bool = True,
    hit: float | None = 1.0,
    best: float | None = 0.8,
    latency: float = 100.0,
) -> QuestionResult:
    """A minimal per-question result."""
    return QuestionResult(
        id="q",
        category=category,
        answerable=answerable,
        question="?",
        expected=[],
        hit=hit if answerable else None,
        recall=hit if answerable else None,
        reciprocal_rank=hit if answerable else None,
        best_rerank_score=best,
        below_threshold=False,
        latency_ms=latency,
    )


def test_score_question_flags_each_retrieved_chunk() -> None:
    retrieved = [RetrievedRef("spec.pdf", 3, 3), RetrievedRef("rules.pdf", 90, 92)]
    hit, recall, rr, flags = score_question(make_question(), retrieved, k=5)
    assert (hit, recall, rr) == (1.0, 1.0, 0.5)
    assert flags == [False, True]


def test_score_question_unanswerable_has_no_metrics() -> None:
    hit, recall, rr, flags = score_question(make_question(False), [RetrievedRef("x.pdf", 1, 1)], 5)
    assert (hit, recall, rr) == (None, None, None)
    assert flags == [False]


def test_summarise_averages_overall_and_per_category_skipping_unanswerable() -> None:
    results = [
        make_result("exact_code", hit=1.0, latency=100),
        make_result("exact_code", hit=0.0, latency=300),
        make_result("legal_rule", hit=1.0, latency=200),
        make_result("unanswerable", answerable=False, latency=400),
    ]
    summary = summarise(results)
    overall = summary["overall"]
    assert isinstance(overall, dict)
    assert overall["hit_rate"] == pytest.approx(2 / 3)
    assert overall["questions"] == 4 and overall["answerable"] == 3
    per_category = summary["per_category"]
    assert isinstance(per_category, dict)
    assert per_category["exact_code"]["hit_rate"] == pytest.approx(0.5)
    assert per_category["unanswerable"]["hit_rate"] is None
    latency = summary["latency_ms"]
    assert isinstance(latency, dict)
    assert latency["p50"] == pytest.approx(250)


def test_threshold_sweep_trades_false_refusals_for_caught_unanswerables() -> None:
    results = [
        make_result(best=0.9),
        make_result(best=0.2),
        make_result("unanswerable", answerable=False, best=0.1),
        make_result("unanswerable", answerable=False, best=0.4),
    ]
    rows = {row["threshold"]: row for row in threshold_sweep(results)}
    assert rows[0.15]["false_refusal_rate"] == 0.0
    assert rows[0.15]["unanswerable_refused_rate"] == 0.5
    assert rows[0.3]["false_refusal_rate"] == 0.5
    assert rows[0.5]["unanswerable_refused_rate"] == 1.0


def test_threshold_sweep_is_empty_without_rerank_scores() -> None:
    assert threshold_sweep([make_result(best=None)]) == []


def test_markdown_reports_render_metrics_and_misses() -> None:
    results = [make_result(hit=1.0), make_result("exact_code", hit=0.0)]
    summary = summarise(results)
    text = config_markdown("final", "desc", "tune", "eval_x", 5, summary, [], results)
    assert "hit rate@5 | 0.500" in text
    assert "## Misses" in text and "(exact_code)" in text
    table = comparison_markdown("tune", 5, [("baseline", "b", summary), ("final", "f", summary)])
    assert "| `baseline` | b | 0.500 |" in table
    assert "exact_code" in table


def test_corpus_documents_includes_indexed_and_superseded_only(tmp_path: Path) -> None:
    rows = [
        {"path": "legal/a.pdf", "title": "A", "authority": "law", "index": True},
        {"path": "old/b.pdf", "title": "B", "index": False, "superseded": True},
        {"path": "extra/c.pdf", "title": "C", "index": False},
    ]
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    docs = corpus_documents(manifest)
    assert [doc.filename for doc in docs] == ["a.pdf", "b.pdf"]
    assert docs[1].superseded is True
    assert docs[0].doc_id == corpus_documents(manifest)[0].doc_id  # stable id


def test_cached_blocks_round_trip_through_json() -> None:
    block = ExtractedBlock(
        text="Rule 150V", page=91, section_path=("Chapter XIV", "150V"), kind="text", ocr=True
    )
    restored = _block_from_json(json.loads(json.dumps(asdict(block))))
    assert restored == block
