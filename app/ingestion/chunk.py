"""Chunking: cut extracted blocks into the pieces that get embedded and cited.

Two strategies, compared in the evaluation:

- ``fixed_512``: the baseline. One stream of text cut every 512 tokens with
  50 tokens overlap, ignoring structure. Simple, but it can cut a rule in
  half or separate a table row from its column names.
- ``structure``: cut where the document itself has edges. One legal rule,
  one table row (with its header row repeated), one whole JSON sample, one
  FAQ question and answer, or one caption per chunk; anything still too long
  goes through a recursive splitter (~500 tokens, 75 overlap).

Every chunk keeps the page range it came from, so citations can point at a
page. ``embed_text`` adds a context header ("Doc title > section path") on
top of ``text``: a chunk that only says "within fifteen days" still says
what it is about. Only ``embed_text`` is embedded; ``text`` is what users see.
"""

import bisect
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import settings
from app.guardrails import flag_suspicious
from app.ingestion.extract import ExtractedBlock

Strategy = Literal["structure", "fixed_512"]
ChunkType = Literal["text", "legal_rule", "table_row", "json", "faq", "caption", "fixed"]

# A rule or section heading at the start of a line: "150V. Extension ...",
# "23. Tax invoices.", "[150Q. Application." (the bracket marks an amendment).
# Scanned text often reads "1" as "l" or "I" ("l50XD."), so those count as
# digits too, but only next to at least one real digit ("I. Intro" is no rule).
# In Chapter XIV the number can stand alone on its line ("150X." then the title).
LEGAL_HEADING_RE = re.compile(r"^\s*\[?((?=[\dlI]*\d)[\dlI]{1,3})([A-Z]{0,4})\.(?=\s|$)")
# Titles of legal instruments, whose text is split rule by rule.
LEGAL_TITLE_RE = re.compile(r"\b(act|rules?|s\.?\s?r\.?\s?o\.?|ordinance|notification)\b", re.I)
# A JSON sample has quoted keys: '"invoiceType":'. This tells it apart from
# legal amendment brackets such as "[(3) Retailers ...]".
JSON_KEY_RE = re.compile(r'"[^"\n]+"\s*:')
TABLE_SEPARATOR_RE = re.compile(r"^\|?\s*:?-{3,}")


@dataclass(frozen=True)
class Chunk:
    """One retrievable piece of a document.

    Attributes:
        text: What users see and what citations quote.
        embed_text: ``text`` with the context header on top; this is embedded.
        chunk_index: Position within the document, from 0.
        page_start: First page (or slide / FAQ number); None without pages.
        page_end: Last page; equals ``page_start`` for one-page chunks.
        section_path: Headings above the chunk, outermost first.
        chunk_type: Which structure rule produced it.
        ocr: True if any of its text came from OCR or a caption.
        amended_by: Amendments noted for this text, e.g. "S.R.O. 69(I)/2025".
        suspicious: True if it contains instruction-like text.
    """

    text: str
    embed_text: str
    chunk_index: int
    page_start: int | None
    page_end: int | None
    section_path: tuple[str, ...]
    chunk_type: ChunkType
    ocr: bool = False
    amended_by: tuple[str, ...] = ()
    suspicious: bool = False


@dataclass(frozen=True)
class Line:
    """One line of text with where it came from (so pages survive splitting)."""

    text: str
    page: int | None
    ocr: bool
    amended_by: tuple[str, ...]
    section: tuple[str, ...] = ()


@dataclass(frozen=True)
class Piece:
    """A chunk before numbering: lines plus its type and section."""

    lines: tuple[Line, ...]
    chunk_type: ChunkType
    section_path: tuple[str, ...]


def chunk_blocks(
    blocks: list[ExtractedBlock],
    doc_title: str,
    strategy: Strategy = "structure",
    context_header: bool = True,
) -> list[Chunk]:
    """Cut a document's blocks into chunks.

    Args:
        blocks: Output of extraction, in reading order.
        doc_title: Human-readable title, used in the context header and to
            decide whether the text is legal (split by rule).
        strategy: ``structure`` (the real system) or ``fixed_512`` (baseline).
        context_header: Whether to put "title > section" on top of
            ``embed_text``; an eval config can switch it off to measure it.

    Returns:
        Numbered chunks in document order.
    """
    if strategy == "fixed_512":
        pieces = fixed_pieces(blocks)
    else:
        unit = legal_unit(doc_title)
        pieces = structure_pieces(blocks, unit)
    chunks = []
    for piece in pieces:
        text = "\n".join(line.text for line in piece.lines).strip()
        # Text without a single letter ("21.\n22.", a stray page number) can
        # answer nothing, so it is not worth a chunk.
        if not re.search(r"[^\W\d_]", text):
            continue
        pages = [line.page for line in piece.lines if line.page is not None]
        header = context_header_for(doc_title, piece.section_path)
        chunks.append(
            Chunk(
                text=text,
                embed_text=f"{header}\n\n{text}" if context_header else text,
                chunk_index=len(chunks),
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                section_path=piece.section_path,
                chunk_type=piece.chunk_type,
                ocr=any(line.ocr for line in piece.lines),
                amended_by=_unique(ref for line in piece.lines for ref in line.amended_by),
                suspicious=flag_suspicious(text),
            )
        )
    return chunks


def is_legal_title(title: str) -> bool:
    """Say whether a document title names a law, rules or a notification.

    Args:
        title: Document title or file name.

    Returns:
        True for e.g. "Sales Tax Rules, 2006" or "SRO 69(I)/2025".
    """
    return bool(LEGAL_TITLE_RE.search(title.replace("_", " ")))


def legal_unit(title: str) -> str | None:
    """Name the numbered unit of a legal document, as citations call it.

    Args:
        title: Document title or file name.

    Returns:
        "Section" for an Act ("Section 23"), "Rule" for rules, SROs and
        notifications ("Rule 150V"), None for non-legal documents.
    """
    if not is_legal_title(title):
        return None
    return "Section" if re.search(r"\bact\b", title.replace("_", " "), re.I) else "Rule"


def context_header_for(doc_title: str, section_path: tuple[str, ...]) -> str:
    """Build the "Doc title > section path" line put above a chunk.

    Args:
        doc_title: Document title.
        section_path: Headings above the chunk.

    Returns:
        For example "Sales Tax Rules, 2006 > CHAPTER XIV ... > Rule 150V".
    """
    return " > ".join((doc_title, *section_path))


# --- Strategy: fixed_512 (baseline) -----------------------------------------------


def fixed_pieces(blocks: list[ExtractedBlock]) -> list[Piece]:
    """Cut all text into fixed windows, ignoring structure (the baseline).

    Args:
        blocks: Extracted blocks.

    Returns:
        Windows of ``fixed_chunk_size`` tokens with ``fixed_chunk_overlap``
        overlap, each with the section of the block it starts in.
    """
    lines = [line for block in blocks for line in _lines(block)]
    size = settings.fixed_chunk_size * settings.chars_per_token
    overlap = settings.fixed_chunk_overlap * settings.chars_per_token
    # Splitting on characters only (separator "") gives true fixed windows.
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        separators=[""],
        add_start_index=True,
        strip_whitespace=False,
    )
    return [
        Piece(lines=window, chunk_type="fixed", section_path=window[0].section)
        for window in _split_lines(lines, splitter)
    ]


# --- Strategy: structure ---------------------------------------------------------


def structure_pieces(blocks: list[ExtractedBlock], unit: str | None) -> list[Piece]:
    """Cut blocks at their natural edges.

    Args:
        blocks: Extracted blocks.
        unit: "Rule" or "Section" to split legal text at its numbered
            headings; None for documents that are not legal texts.

    Returns:
        Pieces in document order.
    """
    groups = _group_text_blocks(blocks)
    # Lines of every text group, built once so both passes see the same objects.
    group_lines = [
        [line for block in group for line in _lines(block)] if group[0].kind == "text" else []
        for group in groups
    ]
    rule_lines = find_rule_headings(group_lines) if unit else None

    pieces: list[Piece] = []
    for group, lines in zip(groups, group_lines, strict=True):
        first = group[0]
        if first.kind == "table":
            pieces += table_row_pieces(first)
        elif first.kind in ("faq", "caption"):
            # One question-and-answer or one screen per chunk; split only if huge.
            pieces += _fit(Piece(tuple(_lines(first)), first.kind, first.section_path))
        else:
            pieces += text_pieces(lines, first.section_path, rule_lines, unit or "Rule")
    return pieces


def find_rule_headings(group_lines: list[list[Line]]) -> set[int]:
    """Pick the lines that really start a rule or section in a legal document.

    Legal numbering only goes up through a document (1, 2 ... 150, 150A ...
    150XQ). Lines that merely look like headings, such as list items ("2."
    inside Rule 5) or a stray "161.", break that order. So of all candidate
    lines, the longest sequence whose numbers never go down is kept. Bare
    numbers followed directly by another number (what is left of the table
    of contents) are not candidates at all.

    Args:
        group_lines: Lines of each text group, in document order.

    Returns:
        ``id()`` of every line that starts a rule.
    """
    candidates: list[tuple[int, int]] = []  # (id of line, rule number)
    for lines in group_lines:
        for index, line in enumerate(lines):
            number = rule_number(line.text)
            if number is None:
                continue
            following = lines[index + 1].text if index + 1 < len(lines) else ""
            if _only_heading([line]) and (not following or rule_number(following) is not None):
                continue  # "21." then "22.": a table-of-contents leftover
            candidates.append((id(line), number))
    keep = longest_non_decreasing([number for _, number in candidates])
    return {candidates[index][0] for index in keep}


def rule_number(text: str) -> int | None:
    """Return the number of a rule heading line, or None if it is not one.

    A rule title starts with a capital or a bracket ("5. Registration.",
    "[150Q. Application."), or the number stands alone with the title on the
    next line; a list item usually starts in lower case ("1. name of ...").

    Args:
        text: A line of text.

    Returns:
        150 for "150XD. Functions ...", also for the misread "l50XD.".
    """
    match = LEGAL_HEADING_RE.match(text)
    if not match:
        return None
    rest = text[match.end() :].strip()
    if rest and not (rest[0].isupper() or rest[0] in "[("):
        return None
    return int(_normalise_digits(match.group(1)))


def longest_non_decreasing(values: list[int]) -> list[int]:
    """Find the longest subsequence whose values never go down.

    Patience-sorting method, O(n log n): ``tails[k]`` holds the index of the
    smallest possible last value of a run of length k + 1.

    Args:
        values: Numbers in document order.

    Returns:
        Indexes (into ``values``) of one longest such subsequence, in order.
    """
    tails: list[int] = []
    tail_values: list[int] = []  # values[t] for t in tails, kept sorted
    previous: list[int | None] = [None] * len(values)
    for index, value in enumerate(values):
        # bisect_right: equal values may follow each other (150, 150A, 150B).
        position = bisect.bisect_right(tail_values, value)
        previous[index] = tails[position - 1] if position else None
        if position == len(tails):
            tails.append(index)
            tail_values.append(value)
        else:
            tails[position] = index
            tail_values[position] = value
    result: list[int] = []
    current = tails[-1] if tails else None
    while current is not None:
        result.append(current)
        current = previous[current]
    return result[::-1]


def table_row_pieces(block: ExtractedBlock) -> list[Piece]:
    """One chunk per table row, with the header row repeated on each.

    Args:
        block: A ``table`` block holding a Markdown table.

    Returns:
        One piece per data row; the whole table as text if it has no header.
    """
    rows = [row for row in block.text.splitlines() if row.strip()]
    if len(rows) < 3 or not TABLE_SEPARATOR_RE.match(rows[1].strip()):
        return text_pieces(list(_lines(block)), block.section_path, rule_lines=None)
    header = rows[:2]
    pieces = []
    for row in rows[2:]:
        if not re.search(r"\w", row):
            continue  # an empty row: "| | |"
        # PyMuPDF writes line breaks inside cells as <br>; plain spaces read better.
        text = "\n".join([*header, row]).replace("<br>", " ")
        line = Line(text, block.page, block.ocr, block.amended_by, block.section_path)
        pieces.append(Piece((line,), "table_row", block.section_path))
    return pieces


def text_pieces(
    lines: list[Line],
    section_path: tuple[str, ...],
    rule_lines: set[int] | None,
    unit: str = "Rule",
) -> list[Piece]:
    """Cut running text: whole JSON samples, then rules, then by size.

    Args:
        lines: The section's lines, possibly spanning several pages.
        section_path: The section they belong to.
        rule_lines: For legal text, ``id()`` of the lines that start a rule
            (from `find_rule_headings`); None to skip rule splitting.
        unit: Word used in labels: "Rule 150V" or "Section 23".

    Returns:
        Pieces in order.
    """
    pieces: list[Piece] = []
    for segment, is_json in split_json(lines):
        if is_json:
            pieces += _fit(
                Piece(tuple(segment), "json", section_path),
                max_tokens=settings.max_json_chunk_tokens,
            )
        elif rule_lines is not None:
            for rule, label in split_legal(segment, rule_lines, unit):
                path = (*section_path, label) if label else section_path
                pieces += _fit(Piece(tuple(rule), "legal_rule" if label else "text", path))
        else:
            pieces += _fit(Piece(tuple(segment), "text", section_path))
    return pieces


def split_json(lines: list[Line]) -> list[tuple[list[Line], bool]]:
    """Separate whole JSON samples from the surrounding text.

    A sample starts at a line beginning with ``{`` or ``[`` and ends where the
    brackets balance again. It counts as JSON only if it has quoted keys;
    otherwise (e.g. an amendment in square brackets) it stays text.

    Args:
        lines: Lines of one section.

    Returns:
        (lines, is_json) segments in order.
    """
    segments: list[tuple[list[Line], bool]] = []
    text: list[Line] = []
    index = 0
    while index < len(lines):
        if lines[index].text.lstrip().startswith(("{", "[")):
            end = _json_end(lines, index)
            candidate = lines[index : end + 1] if end is not None else []
            if candidate and JSON_KEY_RE.search("\n".join(line.text for line in candidate)):
                if text:
                    segments.append((text, False))
                    text = []
                segments.append((candidate, True))
                index = end + 1  # type: ignore[operator]  # end is set when candidate is
                continue
        text.append(lines[index])
        index += 1
    if text:
        segments.append((text, False))
    return segments


def split_legal(
    lines: list[Line], rule_lines: set[int], unit: str = "Rule"
) -> list[tuple[list[Line], str | None]]:
    """Split legal text at the lines chosen as rule headings.

    Args:
        lines: Lines of one section of a legal document.
        rule_lines: ``id()`` of the lines that start a rule.
        unit: Word used in labels ("Rule" or "Section").

    Returns:
        (lines, label) per rule, label such as "Rule 150V"; text before the
        first heading gets label None. A part that is only a number is dropped.
    """
    parts: list[tuple[list[Line], str | None]] = []
    for line in lines:
        match = LEGAL_HEADING_RE.match(line.text) if id(line) in rule_lines else None
        if match:
            # Only the number part can hold misread ones; "150I" keeps its letter.
            label = f"{unit} {_normalise_digits(match.group(1))}{match.group(2)}"
            parts.append(([line], label))
        elif parts:
            parts[-1][0].append(line)
        else:
            parts.append(([line], None))
    return [(part, label) for part, label in parts if not _only_heading(part)]


def _normalise_digits(number: str) -> str:
    """Undo common OCR misreadings of the digit one ("l50" -> "150")."""
    return number.replace("l", "1").replace("I", "1")


def _only_heading(lines: list[Line]) -> bool:
    """Say whether a part is just a rule number with no text ("21.")."""
    return len(lines) == 1 and re.fullmatch(r"\s*\[?\S+\.\]?\s*", lines[0].text) is not None


def _json_end(lines: list[Line], start: int) -> int | None:
    """Find the line where brackets opened at ``start`` balance again.

    Brackets inside quoted strings do not count.

    Args:
        lines: The lines to scan.
        start: Index of the line that opens the sample.

    Returns:
        Index of the closing line, or None if they never balance.
    """
    depth = 0
    in_string = False
    for index in range(start, len(lines)):
        previous = ""
        for char in lines[index].text:
            if char == '"' and previous != "\\":
                in_string = not in_string
            elif not in_string and char in "{[":
                depth += 1
            elif not in_string and char in "}]":
                depth -= 1
                if depth == 0:
                    return index
            previous = char
    return None


def _fit(piece: Piece, max_tokens: int | None = None) -> list[Piece]:
    """Keep a piece whole if it is small enough, otherwise split it by size.

    Args:
        piece: The piece to check.
        max_tokens: Size limit; defaults to ``settings.chunk_size``.

    Returns:
        The piece itself, or several pieces of the same type and section.
    """
    limit = (max_tokens or settings.chunk_size) * settings.chars_per_token
    if sum(len(line.text) + 1 for line in piece.lines) <= limit:
        return [piece]
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size * settings.chars_per_token,
        chunk_overlap=settings.chunk_overlap * settings.chars_per_token,
        add_start_index=True,
    )
    return [
        Piece(window, piece.chunk_type, piece.section_path)
        for window in _split_lines(list(piece.lines), splitter)
    ]


def _split_lines(
    lines: list[Line], splitter: RecursiveCharacterTextSplitter
) -> list[tuple[Line, ...]]:
    """Split lines with a text splitter, keeping each part's page information.

    The splitter only sees one string. Every part it returns is mapped back to
    the lines it overlaps, and those lines supply pages, OCR and amendments.
    A part that starts or ends mid-line gets exactly its own text.

    Args:
        lines: Lines to split.
        splitter: A splitter created with ``add_start_index=True``.

    Returns:
        One tuple of lines per part, in order.
    """
    joined = "\n".join(line.text for line in lines)
    offsets = []  # (start, end, line) for each line in ``joined``
    position = 0
    for line in lines:
        offsets.append((position, position + len(line.text), line))
        position += len(line.text) + 1
    parts = []
    for document in splitter.create_documents([joined]):
        start = document.metadata["start_index"]
        end = start + len(document.page_content)
        covered = [line for s, e, line in offsets if s < end and e > start] or [lines[-1]]
        first = covered[0]
        # The part's own text, attributed to the first line it touches; the
        # other lines contribute only their page, OCR flag and amendments.
        text_line = Line(
            document.page_content.strip(), first.page, first.ocr, first.amended_by, first.section
        )
        rest = tuple(
            Line("", line.page, line.ocr, line.amended_by, line.section) for line in covered[1:]
        )
        parts.append((text_line, *rest))
    return parts


def _group_text_blocks(blocks: list[ExtractedBlock]) -> list[list[ExtractedBlock]]:
    """Join neighbouring text blocks of the same section (across pages).

    A rule or a JSON sample often continues on the next page; joining lets
    it become one chunk with a page range. Tables, FAQs and captions always
    stand alone.

    Args:
        blocks: Extracted blocks in order.

    Returns:
        Groups of blocks; every non-text block is a group of one.
    """
    groups: list[list[ExtractedBlock]] = []
    for block in blocks:
        previous = groups[-1][-1] if groups else None
        if (
            previous is not None
            and block.kind == "text"
            and previous.kind == "text"
            and block.section_path == previous.section_path
        ):
            groups[-1].append(block)
        else:
            groups.append([block])
    return groups


def _lines(block: ExtractedBlock) -> list[Line]:
    """Split a block into lines that remember the block's page and metadata."""
    return [
        Line(text, block.page, block.ocr, block.amended_by, block.section_path)
        for text in block.text.splitlines()
        if text.strip()
    ]


def _unique(items: Iterable[str]) -> tuple[str, ...]:
    """Return items without duplicates, keeping their first-seen order."""
    return tuple(dict.fromkeys(items))
