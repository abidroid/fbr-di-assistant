"""Retrieval metrics: hit@k, recall@k and reciprocal rank, plus averaging.

Every function here is pure (no I/O), so it is cheap to unit test and gives
the same number every time for the same input.

Labels use document + pages, never chunk ids, so the same golden set can grade
any chunking strategy fairly. A retrieved chunk "hits" a source when it comes
from the same document and its page range covers at least one labelled page.

Per-question functions return ``None`` when a question has no sources
(unanswerable questions): retrieval cannot be right or wrong there, and
counting it as 0 or 1 would distort the averages. Those questions are scored
later on whether the system refuses.

How the reported numbers are built:
    hit rate@k = mean of ``hit_at_k`` over answerable questions
    recall@k   = mean of ``recall_at_k``
    MRR        = mean of ``reciprocal_rank``
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SourceLabel:
    """Where the answer to a golden-set question lives.

    Attributes:
        doc: File name of the document, e.g. ``sales_tax_rules_2006_upto_2026-07-31.pdf``.
        pages: Physical page numbers (1-based) holding the answer. For
            ``faqs.jsonl`` this is the FAQ number.
    """

    doc: str
    pages: tuple[int, ...]


@dataclass(frozen=True)
class RetrievedRef:
    """The location metadata of one retrieved chunk, in rank order.

    Attributes:
        doc: File name of the document the chunk came from.
        page_start: First physical page the chunk covers.
        page_end: Last physical page the chunk covers (same as start for one page).
    """

    doc: str
    page_start: int
    page_end: int


def is_hit(chunk: RetrievedRef, source: SourceLabel) -> bool:
    """Decide whether one retrieved chunk matches one labelled source.

    Args:
        chunk: A retrieved chunk's location.
        source: A labelled source of the answer.

    Returns:
        True if both name the same document and the chunk's page range covers
        at least one of the source's pages.
    """
    if chunk.doc != source.doc:
        return False
    return any(chunk.page_start <= page <= chunk.page_end for page in source.pages)


def _top_k(retrieved: Sequence[RetrievedRef], k: int) -> Sequence[RetrievedRef]:
    """Return the first ``k`` retrieved chunks after checking ``k``.

    Args:
        retrieved: Chunks in rank order (best first).
        k: How many to keep.

    Returns:
        At most ``k`` chunks; fewer if fewer were retrieved.

    Raises:
        ValueError: If ``k`` is smaller than 1, which is always a caller bug.
    """
    if k < 1:
        raise ValueError(f"k must be at least 1, got {k}")
    return retrieved[:k]


def hit_at_k(
    retrieved: Sequence[RetrievedRef], sources: Sequence[SourceLabel], k: int
) -> float | None:
    """Score whether ANY correct source appears in the top ``k``.

    Args:
        retrieved: Chunks in rank order (best first).
        sources: The question's labelled sources.
        k: Cut-off rank.

    Returns:
        1.0 if at least one top-k chunk hits any source, 0.0 if none does,
        None if the question has no sources (unanswerable).
    """
    if not sources:
        return None
    top = _top_k(retrieved, k)
    found = any(is_hit(chunk, source) for chunk in top for source in sources)
    return 1.0 if found else 0.0


def recall_at_k(
    retrieved: Sequence[RetrievedRef], sources: Sequence[SourceLabel], k: int
) -> float | None:
    """Score what FRACTION of the correct sources appear in the top ``k``.

    Each source counts once, however many chunks hit it, so three chunks from
    the same page cannot inflate recall.

    Args:
        retrieved: Chunks in rank order (best first).
        sources: The question's labelled sources.
        k: Cut-off rank.

    Returns:
        Covered sources divided by all sources (0.0 to 1.0), or None if the
        question has no sources.
    """
    if not sources:
        return None
    top = _top_k(retrieved, k)
    covered = sum(1 for source in sources if any(is_hit(chunk, source) for chunk in top))
    return covered / len(sources)


def reciprocal_rank(
    retrieved: Sequence[RetrievedRef], sources: Sequence[SourceLabel], k: int
) -> float | None:
    """Score how HIGH the first correct chunk is ranked: 1 / its rank.

    Args:
        retrieved: Chunks in rank order (best first).
        sources: The question's labelled sources.
        k: Only ranks 1..k count; a first hit below ``k`` scores 0.

    Returns:
        1.0 for rank 1, 0.5 for rank 2, 0.33 for rank 3, ..., 0.0 if no top-k
        chunk hits, or None if the question has no sources.
    """
    if not sources:
        return None
    for rank, chunk in enumerate(_top_k(retrieved, k), start=1):
        if any(is_hit(chunk, source) for source in sources):
            return 1.0 / rank
    return 0.0


def mean_ignoring_none(values: Iterable[float | None]) -> float | None:
    """Average per-question scores, skipping questions where the metric is undefined.

    Args:
        values: Per-question scores; None marks an unanswerable question.

    Returns:
        The mean of the non-None values, or None if there are none.
    """
    scores = [value for value in values if value is not None]
    if not scores:
        return None
    return sum(scores) / len(scores)
