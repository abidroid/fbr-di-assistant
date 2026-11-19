"""Checks on the real golden set file: it loads, has the planned mix and points at real pages.

These tests guard the answer key itself. A typo in a file name or a page number would
silently turn a correct retrieval into a "miss", so every label is checked against
`corpus/manifest.jsonl` (and `corpus/faqs.jsonl` for FAQ numbers).
"""

import json
from collections import Counter
from pathlib import Path

import pytest

from evals.golden_set import GOLDEN_SET_PATH, Category, GoldenQuestion, load_golden_set

CORPUS_DIR = Path(__file__).resolve().parents[2] / "corpus"
FAQ_DOC = "faqs.jsonl"

# (tune, heldout) per category, as agreed in the Phase 3 plan.
EXPECTED_SPLIT: dict[Category, tuple[int, int]] = {
    Category.EXACT_CODE: (4, 3),
    Category.FIELD_RULE: (4, 3),
    Category.LEGAL_RULE: (4, 3),
    Category.REWORDED_FAQ: (3, 2),
    Category.TABLE_MATRIX: (3, 2),
    Category.MULTI_PART: (3, 2),
    Category.SCREENSHOT: (2, 1),
    Category.VERSION_SENSITIVE: (2, 1),
    Category.UNANSWERABLE: (5, 3),
}


@pytest.fixture(scope="module")
def questions() -> list[GoldenQuestion]:
    """Load the committed golden set once for all tests in this module.

    Returns:
        Every question in `evals/golden_set.jsonl`.
    """
    return load_golden_set(GOLDEN_SET_PATH)


@pytest.fixture(scope="module")
def page_counts() -> dict[str, int]:
    """Map each indexed, current corpus file name to its number of pages.

    Superseded and non-indexed files are left out on purpose: a label pointing at
    them could never be retrieved. For the FAQ file the "page count" is the number
    of numbered FAQs, because FAQ chunks use the FAQ number as their page.

    Returns:
        File name (no folder) -> highest valid page number.
    """
    counts: dict[str, int] = {}
    with (CORPUS_DIR / "manifest.jsonl").open(encoding="utf-8") as manifest:
        for line in manifest:
            entry = json.loads(line)
            if entry["index"] and not entry["superseded"] and entry["pages"]:
                counts[Path(entry["path"]).name] = entry["pages"]
    with (CORPUS_DIR / FAQ_DOC).open(encoding="utf-8") as faqs:
        numbers = [json.loads(line)["faq_number"] for line in faqs if line.strip()]
    counts[FAQ_DOC] = max(n for n in numbers if n is not None)
    return counts


def test_golden_set_has_50_questions(questions: list[GoldenQuestion]) -> None:
    """The plan fixes the set at 50 questions."""
    assert len(questions) == 50


def test_split_is_30_tune_20_heldout(questions: list[GoldenQuestion]) -> None:
    """Tuning uses 30 questions; the other 20 are kept back for the final report."""
    assert Counter(q.split for q in questions) == {"tune": 30, "heldout": 20}


def test_category_counts_and_split_match_plan(questions: list[GoldenQuestion]) -> None:
    """Every category has its planned size, divided between tune and heldout as planned."""
    actual = {
        category: (
            sum(1 for q in questions if q.category == category and q.split == "tune"),
            sum(1 for q in questions if q.category == category and q.split == "heldout"),
        )
        for category in Category
    }
    assert actual == EXPECTED_SPLIT


def test_at_least_8_unanswerable(questions: list[GoldenQuestion]) -> None:
    """Enough refusal cases to tune REFUSAL_THRESHOLD in Phase 5."""
    assert sum(1 for q in questions if not q.answerable) >= 8


def test_every_source_is_an_indexed_corpus_file(
    questions: list[GoldenQuestion], page_counts: dict[str, int]
) -> None:
    """Each labelled document is a current, indexed file from the manifest."""
    unknown = {
        (q.id, source.doc)
        for q in questions
        for source in q.sources
        if source.doc not in page_counts
    }
    assert not unknown


def test_every_labelled_page_exists(
    questions: list[GoldenQuestion], page_counts: dict[str, int]
) -> None:
    """Pages are 1-based physical pages within the document (or valid FAQ numbers)."""
    out_of_range = {
        (q.id, source.doc, page)
        for q in questions
        for source in q.sources
        for page in source.pages
        if not 1 <= page <= page_counts.get(source.doc, 0)
    }
    assert not out_of_range
