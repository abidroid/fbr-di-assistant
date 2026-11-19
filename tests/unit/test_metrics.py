"""Unit tests for evals/metrics.py: pure functions, no database or models."""

from collections.abc import Callable, Sequence

import pytest

from evals.metrics import (
    RetrievedRef,
    SourceLabel,
    hit_at_k,
    is_hit,
    mean_ignoring_none,
    recall_at_k,
    reciprocal_rank,
)

RULES = "sales_tax_rules_2006_upto_2026-07-31.pdf"
SPEC = "di_api_technical_spec_v1.12.pdf"

# Shape shared by hit_at_k, recall_at_k and reciprocal_rank.
Metric = Callable[[Sequence[RetrievedRef], Sequence[SourceLabel], int], float | None]


def chunk(doc: str, start: int, end: int | None = None) -> RetrievedRef:
    """Build a retrieved chunk; ``end`` defaults to ``start`` (one page)."""
    return RetrievedRef(doc=doc, page_start=start, page_end=end if end is not None else start)


# --- is_hit -------------------------------------------------------------------


def test_is_hit_same_doc_same_page() -> None:
    assert is_hit(chunk(RULES, 91), SourceLabel(RULES, (91,)))


def test_is_hit_chunk_spanning_pages_covers_label() -> None:
    assert is_hit(chunk(RULES, 90, 92), SourceLabel(RULES, (91,)))


def test_is_hit_touching_edge_counts() -> None:
    # The chunk ends exactly on the labelled page.
    assert is_hit(chunk(RULES, 89, 91), SourceLabel(RULES, (91, 92)))


def test_is_hit_adjacent_page_does_not_count() -> None:
    assert not is_hit(chunk(RULES, 90), SourceLabel(RULES, (91,)))


def test_is_hit_other_document_never_counts() -> None:
    assert not is_hit(chunk(SPEC, 91), SourceLabel(RULES, (91,)))


def test_is_hit_non_contiguous_pages_skip_the_gap() -> None:
    source = SourceLabel(RULES, (10, 40))
    assert is_hit(chunk(RULES, 40), source)
    assert not is_hit(chunk(RULES, 20), source)


# --- worked example from the briefing -----------------------------------------
# Two sources A and B; only A is in the top 5, at rank 3.

SOURCES_AB = [SourceLabel(RULES, (91,)), SourceLabel(SPEC, (12,))]
RETRIEVED_A_AT_3 = [
    chunk(RULES, 50),
    chunk(SPEC, 3),
    chunk(RULES, 91),  # A
    chunk(RULES, 60),
    chunk(SPEC, 40),
    chunk(SPEC, 12),  # B, but at rank 6: outside the top 5
]


def test_worked_example_hit_at_5() -> None:
    assert hit_at_k(RETRIEVED_A_AT_3, SOURCES_AB, k=5) == 1.0


def test_worked_example_recall_at_5() -> None:
    assert recall_at_k(RETRIEVED_A_AT_3, SOURCES_AB, k=5) == 0.5


def test_worked_example_reciprocal_rank() -> None:
    assert reciprocal_rank(RETRIEVED_A_AT_3, SOURCES_AB, k=5) == pytest.approx(1 / 3)


def test_larger_k_reaches_second_source() -> None:
    assert recall_at_k(RETRIEVED_A_AT_3, SOURCES_AB, k=6) == 1.0


# --- misses, k and empty retrieval --------------------------------------------


def test_no_hit_scores_zero_everywhere() -> None:
    retrieved = [chunk(SPEC, 1), chunk(SPEC, 2)]
    sources = [SourceLabel(RULES, (91,))]
    assert hit_at_k(retrieved, sources, k=5) == 0.0
    assert recall_at_k(retrieved, sources, k=5) == 0.0
    assert reciprocal_rank(retrieved, sources, k=5) == 0.0


def test_first_hit_below_k_scores_zero_rr() -> None:
    retrieved = [chunk(SPEC, 1), chunk(SPEC, 2), chunk(RULES, 91)]
    assert reciprocal_rank(retrieved, [SourceLabel(RULES, (91,))], k=2) == 0.0


def test_k_larger_than_retrieved_list_is_fine() -> None:
    assert hit_at_k([chunk(RULES, 91)], [SourceLabel(RULES, (91,))], k=10) == 1.0


def test_empty_retrieval_scores_zero() -> None:
    sources = [SourceLabel(RULES, (91,))]
    assert hit_at_k([], sources, k=5) == 0.0
    assert recall_at_k([], sources, k=5) == 0.0
    assert reciprocal_rank([], sources, k=5) == 0.0


@pytest.mark.parametrize("metric", [hit_at_k, recall_at_k, reciprocal_rank])
def test_k_below_one_is_rejected(metric: Metric) -> None:
    with pytest.raises(ValueError):
        metric([chunk(RULES, 91)], [SourceLabel(RULES, (91,))], k=0)


# --- unanswerable questions (no sources) --------------------------------------


@pytest.mark.parametrize("metric", [hit_at_k, recall_at_k, reciprocal_rank])
def test_no_sources_returns_none(metric: Metric) -> None:
    assert metric([chunk(RULES, 91)], [], k=5) is None


# --- duplicates ---------------------------------------------------------------


def test_duplicate_hits_on_one_source_count_once_in_recall() -> None:
    retrieved = [chunk(RULES, 91), chunk(RULES, 91), chunk(RULES, 90, 92)]
    sources = [SourceLabel(RULES, (91,)), SourceLabel(SPEC, (12,))]
    assert recall_at_k(retrieved, sources, k=5) == 0.5


def test_one_chunk_can_cover_two_sources() -> None:
    retrieved = [chunk(RULES, 90, 92)]
    sources = [SourceLabel(RULES, (90,)), SourceLabel(RULES, (92,))]
    assert recall_at_k(retrieved, sources, k=1) == 1.0


# --- averaging ----------------------------------------------------------------


def test_mean_ignoring_none_skips_unanswerable() -> None:
    assert mean_ignoring_none([1.0, None, 0.0, None]) == 0.5


def test_mean_ignoring_none_all_none_returns_none() -> None:
    assert mean_ignoring_none([None, None]) is None


def test_mean_ignoring_none_empty_returns_none() -> None:
    assert mean_ignoring_none([]) is None
