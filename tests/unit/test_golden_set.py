"""Checks on the real golden set file: it loads, has the planned mix and points at real pages.

These tests guard the answer key itself. A typo in a file name or a page number would
silently turn a correct retrieval into a "miss", so every label is checked against
`corpus/manifest.jsonl` (and `corpus/faqs.jsonl` for FAQ numbers).
"""

import json
from collections import Counter
from pathlib import Path

import pytest

from evals.golden_set import (
    GOLDEN_SET_PATH,
    Category,
    GoldenQuestion,
    check_translations,
    load_golden_set,
)

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


def originals(questions: list[GoldenQuestion]) -> list[GoldenQuestion]:
    """Return the questions that are not translations (the planned 50)."""
    return [q for q in questions if q.translation_of is None]


def test_golden_set_has_50_original_questions(questions: list[GoldenQuestion]) -> None:
    """The plan fixes the set at 50 questions, plus the Urdu translations."""
    assert len(originals(questions)) == 50


def test_split_is_30_tune_20_heldout(questions: list[GoldenQuestion]) -> None:
    """Tuning uses 30 questions; the other 20 are kept back for the final report."""
    assert Counter(q.split for q in originals(questions)) == {"tune": 30, "heldout": 20}


def test_category_counts_and_split_match_plan(questions: list[GoldenQuestion]) -> None:
    """Every category has its planned size, divided between tune and heldout as planned."""
    base = originals(questions)
    actual = {
        category: (
            sum(1 for q in base if q.category == category and q.split == "tune"),
            sum(1 for q in base if q.category == category and q.split == "heldout"),
        )
        for category in Category
    }
    assert actual == EXPECTED_SPLIT


def test_eight_urdu_translations_spread_over_categories(
    questions: list[GoldenQuestion],
) -> None:
    """Phase 10 adds 8 Urdu translations of English questions, in 8 different categories."""
    translations = [q for q in questions if q.translation_of is not None]
    assert len(translations) == 8
    assert all(q.language == "ur" for q in translations)
    assert len({q.category for q in translations}) == 8
    assert Counter(q.split for q in translations) == {"tune": 5, "heldout": 3}


def test_translation_must_match_its_original() -> None:
    """A translation with a different split would make the paired comparison unfair."""
    english = GoldenQuestion(
        id="q001",
        question="What is error 0052?",
        category=Category.EXACT_CODE,
        answerable=True,
        sources=[{"doc": "a.pdf", "pages": [1]}],
        reference_answer="HS code mismatch.",
        split="tune",
    )
    urdu = english.model_copy(
        update={"id": "q051", "language": "ur", "translation_of": "q001", "split": "heldout"}
    )
    with pytest.raises(ValueError, match="must match q001"):
        check_translations([english, urdu])
    with pytest.raises(ValueError, match="must name an original"):
        check_translations([urdu.model_copy(update={"translation_of": "q999"})])


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
