"""Load and validate the golden set (``evals/golden_set.jsonl``).

One JSON object per line, one question per object. Pydantic checks every line
on load, so a typo in a category or a missing page number fails loudly here
instead of silently skewing an evaluation run later.
"""

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from evals.metrics import SourceLabel

GOLDEN_SET_PATH = Path(__file__).parent / "golden_set.jsonl"


class Category(StrEnum):
    """The question types the golden set covers (see evals/README.md)."""

    EXACT_CODE = "exact_code"
    FIELD_RULE = "field_rule"
    LEGAL_RULE = "legal_rule"
    REWORDED_FAQ = "reworded_faq"
    TABLE_MATRIX = "table_matrix"
    MULTI_PART = "multi_part"
    SCREENSHOT = "screenshot"
    VERSION_SENSITIVE = "version_sensitive"
    UNANSWERABLE = "unanswerable"


class Source(BaseModel):
    """One place in the corpus that holds (part of) the answer.

    Attributes:
        doc: File name as in ``corpus/manifest.jsonl`` (without the folder).
        pages: Physical PDF pages, 1-based; for ``faqs.jsonl`` the FAQ number.
        section: Human-readable pointer such as "Rule 150V", for reviewers only.
    """

    doc: str = Field(min_length=1)
    pages: list[int] = Field(min_length=1)
    section: str = ""

    def to_label(self) -> SourceLabel:
        """Convert to the frozen form the metric functions take.

        Returns:
            A `SourceLabel` with the same document and pages.
        """
        return SourceLabel(doc=self.doc, pages=tuple(self.pages))


class GoldenQuestion(BaseModel):
    """One evaluation question with its labelled sources and reference answer.

    Attributes:
        id: Stable identifier such as ``q017``.
        question: Worded the way an ERP/POS developer would ask it.
        category: Question type, used to report results per category.
        answerable: False when the corpus does not contain the answer.
        language: ``en`` now; ``ur`` for the Urdu questions in Phase 10.
        sources: Where the answer lives; empty exactly when unanswerable.
        reference_answer: The correct answer in one or two sentences.
        split: ``tune`` (used while tuning) or ``heldout`` (reported only).
    """

    id: str = Field(pattern=r"^q\d{3}$")
    question: str = Field(min_length=5)
    category: Category
    answerable: bool
    language: Literal["en", "ur"] = "en"
    sources: list[Source]
    reference_answer: str = Field(min_length=1)
    split: Literal["tune", "heldout"]

    @model_validator(mode="after")
    def sources_match_answerable(self) -> "GoldenQuestion":
        """Check that answerable questions have sources and unanswerable ones do not.

        Returns:
            The validated question.

        Raises:
            ValueError: If ``answerable``, ``category`` and ``sources`` disagree.
        """
        is_unanswerable_category = self.category is Category.UNANSWERABLE
        if self.answerable == is_unanswerable_category:
            raise ValueError("category 'unanswerable' must go with answerable=false and only then")
        if self.answerable and not self.sources:
            raise ValueError("an answerable question needs at least one source")
        if not self.answerable and self.sources:
            raise ValueError("an unanswerable question must have no sources")
        return self

    def labels(self) -> list[SourceLabel]:
        """Return the sources in the form the metric functions take.

        Returns:
            One `SourceLabel` per source (empty for unanswerable questions).
        """
        return [source.to_label() for source in self.sources]


def load_golden_set(path: Path = GOLDEN_SET_PATH) -> list[GoldenQuestion]:
    """Read and validate every question in a golden-set file.

    Args:
        path: The JSONL file to read; defaults to ``evals/golden_set.jsonl``.

    Returns:
        The questions in file order.

    Raises:
        ValueError: If a line is invalid (the message names the line number)
            or two questions share an id.
    """
    questions: list[GoldenQuestion] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue  # tolerate blank lines, e.g. a trailing newline
        try:
            question = GoldenQuestion.model_validate_json(line)
        except ValueError as exc:
            raise ValueError(f"{path.name} line {line_number}: {exc}") from exc
        if question.id in seen_ids:
            raise ValueError(f"{path.name} line {line_number}: duplicate id {question.id}")
        seen_ids.add(question.id)
        questions.append(question)
    return questions
