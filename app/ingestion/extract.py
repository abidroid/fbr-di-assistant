"""Text extraction: turn a validated file into a list of `ExtractedBlock`.

One reader per kind of content, chosen by measured speed and quality on the
real corpus (numbers in DESIGN.md):

- PDF pages with a text layer: PyMuPDF, about 5 ms per page. It also finds
  tables (``find_tables``, run only on pages that have ruling lines) and
  returns them as Markdown.
- PDF pages without a text layer: mostly images -> captioned by the small
  Claude model; otherwise -> Docling OCR (8-17 s per page on CPU, so rare).
- DOCX/PPTX: Docling (no ML models needed for these, ~0.1 s); python-pptx is
  the fallback for slides Docling cannot read.
- TXT/MD: split on Markdown headings.

PDF clean-up done here, so chunks only hold real content: running headers
and footers are dropped, superscript footnote markers are removed (they would
add stray numbers such as "236" next to "section 236G"), amendment footnotes
move into ``amended_by`` metadata, other footnotes become their own block.
"""

import asyncio
import io
import json
import logging
import re
import threading
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import anyio
import pymupdf

from app.config import settings
from app.exceptions import InvalidDocumentError
from app.ingestion.caption import Captioner
from app.ingestion.validate import ValidatedFile

if TYPE_CHECKING:  # only for type hints; the real import is slow (torch)
    from docling_core.types.doc import DoclingDocument

logger = logging.getLogger(__name__)

BlockKind = Literal["text", "table", "faq", "caption"]

# A footnote that changes the law ("Substituted by S.R.O. 69(I)/2025") goes to
# amended_by; any other footnote is kept as text.
AMENDMENT_RE = re.compile(
    r"\b(substituted|added|inserted|omitted|amended|renumbered|deleted)\b", re.IGNORECASE
)
# The citable part of an amendment note: an SRO number or a Finance Act year.
AMENDMENT_REF_RE = re.compile(
    r"S\.\s*R\.\s*O\.?\s*(?:No\.?\s*)?\d+\s*\(\s*I\s*\)\s*/\s*\d{4}|Finance\s+Act,?\s+\d{4}",
    re.IGNORECASE,
)
MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
# A numbered section heading: "5. Reference APIs", "4.2.5.Http Status Codes:".
NUMBERED_HEADING_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2})*)\.\s*[A-Za-z]")
# Legal rules look like "69. Remuneration. – (1) ...": a heading run into its
# text. The chunker splits those; here they must stay body text.
LEGAL_RULE_STYLE_RE = re.compile(r"\.\s*[–—−-]")
# Dashes legal texts put after a rule title: en, em, minus sign, hyphen.
DASHES = ("–", "—", "−", "-")
# Words that always open a new legal division, never continue a title.
NEW_DIVISION_RE = re.compile(r"^(CHAPTER|PART|SUB-CHAPTER)\b", re.IGNORECASE)
# Big-font pages before the content; they are titles, not sections.
FRONT_MATTER = {
    "table of contents",
    "table of content",
    "contents",
    "document information",
    "document changelog",
    "index",
}
# A table-of-contents line: dot leaders then a page number ("Audit. ...... 91").
# TOC entries would compete with the real rule in search, so they are dropped.
TOC_LINE_RE = re.compile(r"\.{5,}\s*\d+\s*$")
# A table cell that is data, not a column name: "1.", "0022", "SN001", "12-B".
CODE_CELL_RE = re.compile(r"[\d.\s-]+|[A-Z]{1,4}\d{2,}[A-Z]?|\d+[A-Za-z]?")
# Private-use characters are icon-font glyphs (bullets such as U+F0B7) that
# carry no meaning and would only add noise to embeddings.
PRIVATE_USE_RE = re.compile("[-]")
# Table pre-check: a table needs at least this many short ruling lines in each
# direction. Measured: finds all 36 table pages of the API spec at 1/100 the
# cost of running find_tables on every page.
MIN_GRID_LINES = 3
# Lines longer than this share of the page are borders, not table rules.
MAX_RULE_SHARE = 0.85
# Headings are short; a long bold paragraph is still body text.
MAX_HEADING_CHARS = 200


@dataclass(frozen=True)
class ExtractedBlock:
    """A piece of a document with where it came from.

    Attributes:
        text: The content (tables as Markdown).
        page: 1-based page or slide number; FAQ number for FAQ records; None
            when the format has no pages (DOCX, TXT, MD).
        section_path: Headings above this text, outermost first.
        kind: What the text is; the chunker treats each kind differently.
        ocr: True if the text did not come from a text layer (OCR or caption).
        amended_by: Amendments noted in footnotes, e.g. "S.R.O. 69(I)/2025".
    """

    text: str
    page: int | None
    section_path: tuple[str, ...] = ()
    kind: BlockKind = "text"
    ocr: bool = False
    amended_by: tuple[str, ...] = ()


@dataclass
class ExtractionResult:
    """Everything extraction learned about a document.

    Attributes:
        blocks: Content in reading order.
        page_count: Pages or slides; None for formats without pages.
        ocr_pages: Pages read by OCR or captioning instead of a text layer.
        warnings: Non-fatal problems, stored on the document row.
        title: Title from the file's own metadata, if it has a usable one.
    """

    blocks: list[ExtractedBlock]
    page_count: int | None
    ocr_pages: int = 0
    warnings: list[str] = field(default_factory=list)
    title: str | None = None


@dataclass
class PdfScan:
    """Result of reading a PDF's text layer, before OCR and captions.

    Attributes:
        blocks: Blocks from pages that have a text layer.
        caption_pages: Pages with no text that are mostly images.
        ocr_pages: Pages with no text that are not mostly images.
        page_count: Number of pages.
        title: Usable title from the PDF metadata, if any.
    """

    blocks: list[ExtractedBlock]
    caption_pages: list[int]
    ocr_pages: list[int]
    page_count: int
    title: str | None


class Extractor:
    """Reads every supported file type; created once in the app's lifespan.

    Docling is imported and built on first use, not at startup, because the
    import alone pulls in torch (several seconds) and plain-text PDFs never
    need it. A lock makes sure only one Docling conversion runs at a time: it
    uses all CPU cores, so two at once would just be twice as slow each.
    """

    def __init__(self, captioner: Captioner) -> None:
        """Create the extractor.

        Args:
            captioner: Used for image-only pages (fake in tests).
        """
        self._captioner = captioner
        self._converter: Any = None
        self._docling_lock = threading.Lock()

    async def extract(self, file: ValidatedFile) -> ExtractionResult:
        """Extract blocks from a validated file.

        CPU-heavy reading runs in a worker thread so the event loop stays free
        to serve other requests (including /health) meanwhile.

        Args:
            file: The upload, already checked by `validate_file`.

        Returns:
            Blocks plus page, OCR and warning information.

        Raises:
            InvalidDocumentError: If no readable text was found.
            UpstreamUnavailableError: If captioning failed after retries.
        """
        if file.file_type == "pdf":
            result = await self._extract_pdf(file)
        elif file.file_type in ("docx", "pptx"):
            result = await anyio.to_thread.run_sync(self._extract_office, file)
        else:
            blocks = extract_text_file(file.data.decode("utf-8-sig"))
            result = ExtractionResult(blocks=blocks, page_count=None)

        if not any(block.text.strip() for block in result.blocks):
            raise InvalidDocumentError("No readable text was found in the file.")
        return result

    async def _extract_pdf(self, file: ValidatedFile) -> ExtractionResult:
        """Read a PDF: text layer first, then OCR and captions for the rest.

        Args:
            file: A validated PDF.

        Returns:
            The extraction result with blocks in page order.
        """
        scan = await anyio.to_thread.run_sync(read_pdf_text_layer, file.data)
        blocks = list(scan.blocks)
        warnings: list[str] = []

        if scan.ocr_pages:
            ocr_blocks = await anyio.to_thread.run_sync(self._ocr_pages, file.data, scan.ocr_pages)
            blocks.extend(ocr_blocks)
            read = {block.page for block in ocr_blocks}
            warnings += [
                f"Page {p}: no text found, even with OCR." for p in scan.ocr_pages if p not in read
            ]
        if scan.caption_pages:
            blocks.extend(await self._caption_pages(file, scan.caption_pages))

        # Python's sort is stable: blocks keep their reading order within a page.
        blocks.sort(key=lambda block: block.page or 0)
        return ExtractionResult(
            blocks=blocks,
            page_count=scan.page_count,
            ocr_pages=len(scan.ocr_pages) + len(scan.caption_pages),
            warnings=warnings,
            title=scan.title,
        )

    async def _caption_pages(self, file: ValidatedFile, pages: list[int]) -> list[ExtractedBlock]:
        """Caption image-only pages, several at a time.

        Args:
            file: The PDF being read.
            pages: 1-based numbers of the pages to caption.

        Returns:
            One caption block per page that got a non-empty caption.
        """
        images = await anyio.to_thread.run_sync(render_pages_png, file.data, pages)
        # The calls are independent network waits, so run them concurrently;
        # the semaphore caps how many are in flight at once.
        limit = asyncio.Semaphore(settings.caption_concurrency)

        async def caption_one(page: int, png: bytes) -> ExtractedBlock:
            async with limit:
                text = await self._captioner.caption(png, page, file.filename)
            return ExtractedBlock(text=text, page=page, kind="caption", ocr=True)

        results = await asyncio.gather(*(caption_one(p, png) for p, png in images.items()))
        return [block for block in results if block.text.strip()]

    def _ocr_pages(self, data: bytes, pages: list[int]) -> list[ExtractedBlock]:
        """OCR the given pages with Docling, in one conversion.

        Only these pages are copied into a small temporary PDF, so Docling never
        spends time on pages PyMuPDF has already read.

        Args:
            data: The whole PDF.
            pages: 1-based numbers of the pages to OCR.

        Returns:
            Blocks with ``ocr=True`` and their original page numbers.
        """
        with pymupdf.open(stream=data, filetype="pdf") as source, pymupdf.open() as subset:
            for page in pages:
                subset.insert_pdf(source, from_page=page - 1, to_page=page - 1)
            subset_bytes = subset.tobytes()
        try:
            document = self._convert("ocr-pages.pdf", subset_bytes)
        except InvalidDocumentError:
            logger.warning("Docling OCR failed for %d page(s)", len(pages))
            return []
        # Page i of the temporary PDF is original page pages[i - 1].
        page_map = {index: page for index, page in enumerate(pages, start=1)}
        return docling_to_blocks(document, page_map=page_map, ocr=True)

    def _extract_office(self, file: ValidatedFile) -> ExtractionResult:
        """Read DOCX or PPTX with Docling (python-pptx as the PPTX fallback).

        Args:
            file: A validated DOCX or PPTX.

        Returns:
            The extraction result.

        Raises:
            InvalidDocumentError: If the file cannot be read at all.
        """
        try:
            document = self._convert(file.filename, file.data)
            blocks = docling_to_blocks(document)
        except InvalidDocumentError:
            if file.file_type != "pptx":
                raise
            logger.warning("Docling could not read %s; using python-pptx", file.filename)
            blocks = read_pptx_fallback(file.data)
        return ExtractionResult(blocks=blocks, page_count=file.page_count)

    def _convert(self, name: str, data: bytes) -> "DoclingDocument":
        """Convert bytes with Docling, building the converter on first use.

        Args:
            name: File name; Docling picks its reader from the extension.
            data: The file bytes.

        Returns:
            Docling's parsed document.

        Raises:
            InvalidDocumentError: If Docling cannot convert the file.
        """
        from docling.datamodel.base_models import ConversionStatus, DocumentStream
        from docling.document_converter import DocumentConverter

        with self._docling_lock:
            if self._converter is None:
                self._converter = DocumentConverter()
            try:
                result = self._converter.convert(DocumentStream(name=name, stream=io.BytesIO(data)))
            except Exception as exc:  # Docling raises many types for bad input
                raise InvalidDocumentError(f"The file could not be read: {exc}") from exc
        if result.status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
            raise InvalidDocumentError("The file could not be read.")
        return result.document


# --- PDF text layer (PyMuPDF) -------------------------------------------------


@dataclass(frozen=True)
class BodyStyle:
    """The normal text style of a PDF, the yardstick for everything else.

    Attributes:
        size: Most common font size in points.
        font: Most common font name.
    """

    size: float
    font: str


@dataclass
class PdfContext:
    """What reading one page needs to know about the whole document.

    Attributes:
        body: The normal text style.
        repeated: Normalised running header/footer lines to drop.
        size_levels: Heading font size -> level, from `heading_size_levels`.
        headings: The current heading path, (level, text) outermost first.
            It carries over pages: a chapter heading still applies later.
        table_header: Header row of the last real table, for continuation
            tables (a long table goes on over pages without repeating it).
    """

    body: BodyStyle
    repeated: set[str]
    size_levels: dict[float, int]
    headings: list[tuple[int, str]] = field(default_factory=list)
    table_header: list[str] | None = None


def read_pdf_text_layer(data: bytes) -> PdfScan:
    """Read every PDF page that has a text layer and sort out the rest.

    Args:
        data: PDF bytes.

    Returns:
        Blocks from text pages, plus which pages need captions or OCR.
    """
    with pymupdf.open(stream=data, filetype="pdf") as document:
        body = body_style(document)
        scanned = [is_scanned_page(page) for page in document]
        # In a scan the OCR layer sizes each line to match the image, so font
        # sizes are noise, not headings; rule splitting still structures it.
        mostly_scanned = sum(scanned) > document.page_count / 2
        context = PdfContext(
            body=body,
            repeated=repeated_margin_lines(document),
            size_levels={} if mostly_scanned else heading_size_levels(document, body),
        )
        blocks: list[ExtractedBlock] = []
        caption_pages: list[int] = []
        ocr_pages: list[int] = []
        for page in document:
            number = page.number + 1
            if len(page.get_text().strip()) < settings.min_text_chars_per_page:
                if image_coverage(page) >= settings.caption_min_image_ratio:
                    caption_pages.append(number)
                else:
                    ocr_pages.append(number)
                continue
            page_blocks = read_pdf_page(page, context)
            if scanned[page.number]:
                # The text came from someone else's OCR, so it may contain
                # misreadings ("lncome" for "Income"); say so in the metadata.
                page_blocks = [replace(block, ocr=True) for block in page_blocks]
            blocks.extend(page_blocks)
        return PdfScan(
            blocks=blocks,
            caption_pages=caption_pages,
            ocr_pages=ocr_pages,
            page_count=document.page_count,
            title=_usable_title(document.metadata),
        )


def read_pdf_page(page: pymupdf.Page, context: PdfContext) -> list[ExtractedBlock]:
    """Turn one PDF page into blocks: body text, tables and footnotes.

    Each PyMuPDF block is split into style runs first, because a block can
    hold a heading and the paragraph under it ("2. Data Sharing Mode" in
    14 pt followed by 12 pt text).

    Args:
        page: The page to read.
        context: Document-wide facts; its heading path and table header are
            updated in place, because they carry over to the next page.

    Returns:
        Blocks in reading order.
    """
    number = page.number + 1
    height = page.rect.height
    body, repeated, size_levels = context.body, context.repeated, context.size_levels
    headings = context.headings
    tables = find_page_tables(page)
    table_rects = [rect for rect, _ in tables]
    footnotes: dict[str, str] = {}
    # (y position, block) pairs so tables can be slotted into reading order.
    items: list[tuple[float, ExtractedBlock, frozenset[str]]] = []
    for rect, rows in tables:
        markdown = table_markdown(rows, context)
        if markdown:
            table = ExtractedBlock(markdown, number, _path(headings), kind="table")
            items.append((rect.y0, table, frozenset()))
    pending: list[tuple[float, str, set[str]]] = []  # body text waiting to be merged
    last_level: int | None = None  # level of the run just before, if a heading

    for raw in page.get_text("dict", sort=True)["blocks"]:
        if raw["type"] != 0:  # 0 = text; 1 = image
            continue
        rect = pymupdf.Rect(raw["bbox"])
        plain = "".join(s["text"] for line in raw["lines"] for s in line["spans"]).strip()
        if not re.search(r"\w", plain) or _inside_any(rect, table_rects):
            continue  # decoration such as "..", or text already in a table
        margin = height * settings.pdf_margin_ratio
        if (rect.y1 < margin or rect.y0 > height - margin) and normalise_line(plain) in repeated:
            continue

        runs = style_runs(raw["lines"])
        # A note's number (5 pt) and its text (8 pt) are different styles, so
        # they fall into different runs; collect them and parse them together.
        note_spans: list[dict[str, Any]] = []
        for index, run in enumerate(runs):
            spans = [span for line in run for span in line["spans"]]
            size, _font = _dominant_style(spans)
            y0 = run[0]["bbox"][1]
            if size <= body.size * settings.small_font_ratio and y0 > height / 2:
                note_spans += spans
                continue
            text, markers = _text_without_markers(run, body.size)
            if not text:
                continue
            # Square brackets mark amended text ("[CHAPTER XIV"); not part of a title.
            heading = " ".join(text.split()).strip("[] ")
            after = _run_text(runs[index + 1]) if index + 1 < len(runs) else ""
            level = heading_level(heading, run, body, size_levels, after)
            if level is None:
                last_level = None
                pending.append((y0, text, markers))
                continue
            _flush(pending, items, number, headings)
            if last_level == level and headings and _continues_title(heading):
                # A title split over runs ("CHAPTER XIV" / "PROCEDURE FOR ...").
                headings[-1] = (level, f"{headings[-1][1]} {heading}")
            else:
                _push_heading(headings, level, heading)
            last_level = level
        if note_spans:
            footnotes.update(parse_footnotes(note_spans))

    _flush(pending, items, number, headings)
    items.sort(key=lambda item: item[0])
    # Now every footnote on the page is known, so markers can be resolved.
    blocks = [_with_amendments(block, markers, footnotes) for _, block, markers in items]
    # Footnotes that are not amendments still carry meaning (e.g. "Officer of
    # Sales Tax" now means "Officer of Inland Revenue"), so they stay as text.
    notes = [f"[{n}] {t}" for n, t in footnotes.items() if not AMENDMENT_RE.search(t)]
    if notes:
        blocks.append(
            ExtractedBlock(
                text="Footnotes:\n" + "\n".join(notes), page=number, section_path=_path(headings)
            )
        )
    return blocks


def style_runs(lines: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split a block's lines into runs of lines with the same font and size.

    Args:
        lines: PyMuPDF lines of one block.

    Returns:
        Consecutive groups of lines; a heading line becomes its own group.
    """
    runs: list[list[dict[str, Any]]] = []
    previous: tuple[float, str] | None = None
    for line in lines:
        spans = [span for span in line["spans"] if span["text"].strip()]
        if not spans:
            continue
        style = _dominant_style(spans)
        if runs and style == previous:
            runs[-1].append(line)
        else:
            runs.append([line])
        previous = style
    return runs


def body_style(document: pymupdf.Document) -> BodyStyle:
    """Find the most common font size and font, weighted by characters.

    Args:
        document: An open PDF.

    Returns:
        The body style (12 pt, unknown font if the PDF has no text).
    """
    sizes: Counter[float] = Counter()
    fonts: Counter[str] = Counter()
    for page in document:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    chars = len(span["text"].strip())
                    sizes[round(span["size"], 1)] += chars
                    fonts[span["font"]] += chars
    if not sizes:
        return BodyStyle(size=12.0, font="")
    return BodyStyle(size=sizes.most_common(1)[0][0], font=fonts.most_common(1)[0][0])


def heading_size_levels(document: pymupdf.Document, body: BodyStyle) -> dict[float, int]:
    """Find the font sizes this document uses for headings, and their level.

    A size counts only if short big-font runs in it appear on at least two
    pages. The cover title and front matter ("Table of Contents") are big
    but one-offs; as headings they would sit above every later chapter.
    Very short documents (under 3 pages) keep every big size.

    Args:
        document: An open PDF.
        body: The normal text style.

    Returns:
        Font size (rounded to 0.1 pt) -> level: 1 for the biggest, 2 next...
    """
    pages_per_size: dict[float, set[int]] = {}
    for page in document:
        for block in page.get_text("dict")["blocks"]:
            for run in style_runs(block.get("lines", [])):
                spans = [span for line in run for span in line["spans"]]
                text = " ".join("".join(span["text"] for span in spans).split())
                if not re.search(r"\w", text) or len(text) > MAX_HEADING_CHARS:
                    continue
                if text.lower().strip(" :") in FRONT_MATTER:
                    continue
                size, _font = _dominant_style(spans)
                if size >= body.size * settings.heading_size_ratio:
                    pages_per_size.setdefault(size, set()).add(page.number)
    min_pages = 2 if document.page_count >= 3 else 1
    sizes = sorted(
        (size for size, pages in pages_per_size.items() if len(pages) >= min_pages), reverse=True
    )
    return {size: rank for rank, size in enumerate(sizes, start=1)}


def heading_level(
    text: str,
    run: list[dict[str, Any]],
    body: BodyStyle,
    size_levels: dict[float, int],
    after: str = "",
) -> int | None:
    """Decide whether a style run is a heading and how deep it is.

    Args:
        text: The run's clean text.
        run: Its PyMuPDF lines.
        body: The document's normal text style.
        size_levels: Heading sizes of this document, from `heading_size_levels`.
        after: Text of the next run in the same block, if any.

    Returns:
        The level (1 = outermost), or None for body text.
    """
    if len(text) > MAX_HEADING_CHARS or text.lower().strip(" :") in FRONT_MATTER:
        return None
    spans = [span for line in run for span in line["spans"]]
    size, font = _dominant_style(spans)
    numbered = NUMBERED_HEADING_RE.match(text)
    # A legal rule title ("96. Attachment of ... reasons. – (1) ...") wraps
    # over lines or is followed by a dash; a numbered section heading is one
    # line on its own.
    legal_style = (
        LEGAL_RULE_STYLE_RE.search(text) or len(run) > 1 or after.lstrip().startswith(DASHES)
    )
    if numbered and not legal_style:
        # Headings stand out by being bold (PyMuPDF flag 16), by size, or by
        # using a different font from the body (the API spec does this).
        bold = all(span["flags"] & 16 for span in spans if span["text"].strip())
        if bold or size in size_levels or font != body.font:
            return numbered.group(1).count(".") + 1  # "4.2.5" -> 3
    return size_levels.get(size)


def repeated_margin_lines(document: pymupdf.Document) -> set[str]:
    """Find running headers and footers: margin lines repeated on many pages.

    Args:
        document: An open PDF.

    Returns:
        Normalised text of lines found in the top or bottom margin of more
        than ``settings.repeated_line_ratio`` of the pages (empty for very
        short documents, where "repeated" means nothing).
    """
    if document.page_count < 3:
        return set()
    counts: Counter[str] = Counter()
    for page in document:
        height = page.rect.height
        seen: set[str] = set()
        for block in page.get_text("blocks"):
            x0, y0, x1, y1, text = block[:5]
            margin = height * settings.pdf_margin_ratio
            if y1 < margin or y0 > height - margin:
                seen.add(normalise_line(text))
        counts.update(seen)
    threshold = document.page_count * settings.repeated_line_ratio
    return {line for line, count in counts.items() if count > threshold and line}


def normalise_line(text: str) -> str:
    """Make header/footer lines comparable across pages.

    Digits become ``#`` so "(88)" and "(91)" (printed page numbers) match.

    Args:
        text: A line of text.

    Returns:
        Lower-case text with digits replaced and spaces collapsed.
    """
    return " ".join(re.sub(r"\d+", "#", text.lower()).split())


def image_coverage(page: pymupdf.Page) -> float:
    """Measure how much of a page is covered by images.

    Args:
        page: The page.

    Returns:
        Covered share of the page area, from 0.0 to 1.0.
    """
    area = page.rect.width * page.rect.height
    covered = 0.0
    for info in page.get_image_info():
        clipped = pymupdf.Rect(info["bbox"]) & page.rect
        covered += clipped.width * clipped.height
    return min(covered / area, 1.0) if area else 0.0


def is_scanned_page(page: pymupdf.Page) -> bool:
    """Say whether a page is a scanned image with an OCR text layer on top.

    Args:
        page: The page.

    Returns:
        True if it has text and an image covering almost all of it.
    """
    has_text = len(page.get_text().strip()) >= settings.min_text_chars_per_page
    return has_text and image_coverage(page) >= settings.scanned_page_image_ratio


def has_ruling_grid(page: pymupdf.Page) -> bool:
    """Cheap pre-check: does the page have the lines a table is drawn with?

    Args:
        page: The page.

    Returns:
        True if there are enough short horizontal and vertical lines.
    """
    width, height = page.rect.width, page.rect.height
    horizontal = vertical = 0
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                segments = [(item[1], item[2])]
            elif item[0] == "re":
                r = item[1]
                segments = [(r.tl, r.tr), (r.bl, r.br), (r.tl, r.bl), (r.tr, r.br)]
            else:
                continue
            for a, b in segments:
                dx, dy = abs(a.x - b.x), abs(a.y - b.y)
                if dy < 1 and 5 < dx < MAX_RULE_SHARE * width:
                    horizontal += 1
                elif dx < 1 and 5 < dy < MAX_RULE_SHARE * height:
                    vertical += 1
    return horizontal >= MIN_GRID_LINES and vertical >= MIN_GRID_LINES


def find_page_tables(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, list[list[str]]]]:
    """Find tables on a page and read the text of every cell.

    ``find_tables`` only supplies the cell boxes. Its own cell text drops the
    spaces in these PDFs ("Sellernotregistered"), so each cell is filled from
    the page's normal word list instead, read once per page. The
    ``lines_strict`` strategy ignores coloured cell backgrounds, which the
    default strategy mistakes for extra column lines.

    Args:
        page: The page.

    Returns:
        (area, rows of cell texts) for every table found.
    """
    if not has_ruling_grid(page):
        return []
    try:
        found = page.find_tables(strategy="lines_strict").tables
    except Exception:  # table detection is heuristic; never fail a document on it
        logger.warning("Table detection failed on page %s", page.number + 1, exc_info=True)
        return []
    words = page.get_text("words", sort=True)
    tables = []
    for table in found:
        rows = [[_cell_text(words, cell) for cell in row.cells] for row in table.rows]
        tables.append((pymupdf.Rect(table.bbox), rows))
    return tables


def table_markdown(rows: list[list[str]], context: PdfContext) -> str:
    """Render table rows as Markdown, borrowing the header if it continues.

    A table that runs over several pages repeats no header on later pages,
    so its first row there is data ("0022 | Please provide ..."). Then the
    previous real header with the same number of columns is used.

    Args:
        rows: Cell texts, row by row.
        context: Holds the last real header; updated when a new one is seen.

    Returns:
        A Markdown table, or "" if there is nothing in it.
    """
    rows = [row for row in rows if any(row) and not any(TOC_LINE_RE.search(c) for c in row)]
    if not rows:
        return ""
    if looks_like_header(rows[0]):
        header, data = rows[0], rows[1:]
        context.table_header = header
    elif context.table_header and len(context.table_header) == len(rows[0]):
        header, data = context.table_header, rows
    else:
        header, data = [""] * len(rows[0]), rows

    def line(cells: list[str]) -> str:
        return "|" + "|".join(cell.replace("|", "/") for cell in cells) + "|"

    return "\n".join([line(header), line(["---"] * len(header)), *map(line, data)])


def looks_like_header(row: list[str]) -> bool:
    """Say whether a table row is a header rather than data.

    Args:
        row: Cell texts of the row.

    Returns:
        False if any cell is a number or a code (data rows start "1." or
        "0022" or "SN001"), or if every cell is empty.
    """
    cells = [cell for cell in row if cell]
    return bool(cells) and not any(CODE_CELL_RE.fullmatch(cell) for cell in cells)


def _cell_text(words: list[tuple[Any, ...]], cell: tuple[float, ...] | None) -> str:
    """Join the words whose centre lies inside a table cell.

    Args:
        words: The page's words from ``get_text("words", sort=True)``.
        cell: The cell box, or None for a merged-away cell.

    Returns:
        The cell's text on one line.
    """
    if cell is None:
        return ""
    x0, y0, x1, y1 = cell
    inside = [
        w[4] for w in words if x0 <= (w[0] + w[2]) / 2 <= x1 and y0 <= (w[1] + w[3]) / 2 <= y1
    ]
    return PRIVATE_USE_RE.sub("", " ".join(inside)).strip()


def parse_footnotes(spans: list[dict[str, Any]]) -> dict[str, str]:
    """Split a footnote block into numbered notes.

    In these PDFs a note starts with a superscript number in an even smaller
    font: "²⁴⁸ Sub-rule (3) added by ... ²⁴⁹ Any reference to ...".

    Args:
        spans: The block's PyMuPDF spans.

    Returns:
        Note number -> note text.
    """
    biggest = max(span["size"] for span in spans)
    notes: dict[str, list[str]] = {}
    current: str | None = None
    for span in spans:
        text = span["text"].strip()
        if text.isdigit() and span["size"] < biggest * settings.small_font_ratio:
            current = text
            notes[current] = []
        elif current is not None and text:
            notes[current].append(text)
    return {number: " ".join(parts) for number, parts in notes.items() if parts}


def amendment_refs(note: str) -> list[str]:
    """Extract the citable references from an amendment footnote.

    Args:
        note: Footnote text.

    Returns:
        SRO numbers / Finance Act years; the start of the note if none match.
    """
    refs = [" ".join(match.split()) for match in AMENDMENT_REF_RE.findall(note)]
    return refs or [note[:120]]


def render_pages_png(data: bytes, pages: list[int]) -> dict[int, bytes]:
    """Render pages as PNG images for captioning.

    Args:
        data: PDF bytes.
        pages: 1-based page numbers.

    Returns:
        Page number -> PNG bytes, scaled so the longer side is
        ``settings.caption_image_px``.
    """
    images: dict[int, bytes] = {}
    with pymupdf.open(stream=data, filetype="pdf") as document:
        for number in pages:
            page = document[number - 1]
            zoom = settings.caption_image_px / max(page.rect.width, page.rect.height)
            images[number] = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")
    return images


def _text_without_markers(lines: list[dict[str, Any]], body_size: float) -> tuple[str, set[str]]:
    """Join a block's lines, removing superscript footnote markers.

    Args:
        lines: PyMuPDF lines of one block.
        body_size: Normal text size; markers are much smaller.

    Returns:
        The clean text and the marker numbers that were removed.
    """
    markers: set[str] = set()
    out_lines = []
    for line in lines:
        parts = []
        for span in line["spans"]:
            stripped = span["text"].strip()
            if stripped.isdigit() and span["size"] <= body_size * settings.small_font_ratio:
                markers.add(stripped)
                continue
            parts.append(span["text"])
        joined = " ".join(PRIVATE_USE_RE.sub("", "".join(parts)).split())
        if joined and not TOC_LINE_RE.search(joined):
            out_lines.append(joined)
    return "\n".join(out_lines), markers


def _flush(
    pending: list[tuple[float, str, set[str]]],
    items: list[tuple[float, ExtractedBlock, frozenset[str]]],
    page: int,
    headings: list[tuple[int, str]],
) -> None:
    """Turn waiting body text under the current heading into blocks.

    One block per paragraph, so an amendment marker in one paragraph (Rule
    150N's sub-rule 3) is not attached to the next one (Rule 150O). The
    chunker joins paragraphs of the same section again. Footnote markers
    are kept with each block, not resolved yet: the notes sit at the bottom
    of the page and may not have been read.

    Args:
        pending: Body text collected since the last heading; emptied here.
        items: Output list of (y, block, markers); the blocks are appended.
        page: 1-based page number.
        headings: Current heading path.
    """
    for y0, text, markers in pending:
        block = ExtractedBlock(text=text, page=page, section_path=_path(headings))
        items.append((y0, block, frozenset(markers)))
    pending.clear()


def _with_amendments(
    block: ExtractedBlock, markers: frozenset[str], footnotes: dict[str, str]
) -> ExtractedBlock:
    """Attach the amendments a block's footnote markers point to.

    Args:
        block: A body-text block.
        markers: Footnote numbers removed from its text.
        footnotes: All notes of the page, number -> text.

    Returns:
        The block with ``amended_by`` set (unchanged if none apply).
    """
    amended: list[str] = []
    for marker in sorted(markers, key=int):
        note = footnotes.get(marker, "")
        if AMENDMENT_RE.search(note):
            amended += [ref for ref in amendment_refs(note) if ref not in amended]
    return replace(block, amended_by=tuple(amended)) if amended else block


def _push_heading(headings: list[tuple[int, str]], level: int, text: str) -> None:
    """Add a heading, closing any open heading at the same or a deeper level.

    A new level-1 heading ("5. Reference APIs") replaces the previous level-1
    heading and everything nested below it ("4.2.5 Http Status Codes").

    Args:
        headings: The heading path; changed in place.
        level: Level of the new heading (1 = outermost).
        text: Heading text.
    """
    while headings and headings[-1][0] >= level:
        headings.pop()
    headings.append((level, text))


def _path(headings: list[tuple[int, str]]) -> tuple[str, ...]:
    """Return the heading texts of a heading path."""
    return tuple(text for _, text in headings)


def _dominant_style(spans: list[dict[str, Any]]) -> tuple[float, str]:
    """Return the (font size, font name) covering most characters in spans."""
    styles: Counter[tuple[float, str]] = Counter()
    for span in spans:
        styles[(round(span["size"], 1), span["font"])] += len(span["text"].strip())
    return styles.most_common(1)[0][0]


def _run_text(run: list[dict[str, Any]]) -> str:
    """Return the plain text of a style run."""
    return "".join(span["text"] for line in run for span in line["spans"])


def _continues_title(heading: str) -> bool:
    """Say whether a heading continues the previous one rather than starting anew.

    "PROCEDURE FOR LICENSING ..." continues "CHAPTER XIV"; a new "CHAPTER" or
    "PART" (common after an omitted chapter) and numbered headings never do.
    """
    return not (NEW_DIVISION_RE.match(heading) or NUMBERED_HEADING_RE.match(heading))


def _inside_any(rect: pymupdf.Rect, areas: list[pymupdf.Rect]) -> bool:
    """Say whether most of ``rect`` lies inside one of ``areas``."""
    size = rect.width * rect.height
    for area in areas:
        overlap = rect & area
        if size and overlap.width * overlap.height / size > 0.5:
            return True
    return False


def _usable_title(metadata: dict[str, Any] | None) -> str | None:
    """Return the PDF's metadata title unless it is empty or tool noise."""
    title = ((metadata or {}).get("title") or "").strip()
    if not title or title.lower().startswith(("microsoft word", "untitled")):
        return None
    return title


# --- Docling documents (DOCX, PPTX, OCR pages) -----------------------------------


def docling_to_blocks(
    document: "DoclingDocument", page_map: dict[int, int] | None = None, ocr: bool = False
) -> list[ExtractedBlock]:
    """Convert a Docling document into blocks.

    Args:
        document: Docling's parsed document.
        page_map: Maps Docling page numbers to original ones (OCR subsets).
        ocr: Whether to mark the blocks as OCR text.

    Returns:
        Blocks in reading order: text merged per heading, tables separate.
    """
    from docling_core.types.doc import DocItemLabel, SectionHeaderItem, TableItem

    skip = {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER, DocItemLabel.PICTURE}
    headings: list[tuple[int, str]] = []  # (level, text)
    blocks: list[ExtractedBlock] = []
    pending: list[str] = []
    pending_page: int | None = None

    def flush() -> None:
        if pending:
            blocks.append(
                ExtractedBlock(
                    text="\n".join(pending),
                    page=pending_page,
                    section_path=tuple(t for _, t in headings),
                    ocr=ocr,
                )
            )
            pending.clear()

    for item, _depth in document.iterate_items():
        if item.label in skip:
            continue
        page = _docling_page(item, page_map)
        if item.label in (DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER):
            flush()
            # Title is level 0, the top; section headers carry their own level.
            level = item.level if isinstance(item, SectionHeaderItem) else 0
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, " ".join(item.text.split())))
            continue
        if isinstance(item, TableItem):
            flush()
            markdown = item.export_to_markdown(doc=document).strip()
            if markdown:
                blocks.append(
                    ExtractedBlock(
                        text=markdown,
                        page=page,
                        kind="table",
                        section_path=tuple(t for _, t in headings),
                        ocr=ocr,
                    )
                )
            continue
        text = (getattr(item, "text", "") or "").strip()
        if not text:
            continue
        if pending and page != pending_page:
            flush()  # never merge text across pages: citations need one page
        pending_page = page
        pending.append(f"- {text}" if item.label == DocItemLabel.LIST_ITEM else text)
    flush()
    return blocks


def _docling_page(item: Any, page_map: dict[int, int] | None) -> int | None:
    """Return an item's original 1-based page, or None if it has none."""
    provenance = getattr(item, "prov", None)
    if not provenance:
        return None
    page = provenance[0].page_no
    return page_map.get(page, page) if page_map else page


def read_pptx_fallback(data: bytes) -> list[ExtractedBlock]:
    """Read slides with python-pptx when Docling cannot.

    Args:
        data: PPTX bytes.

    Returns:
        One block per slide with text, titled by the slide title.

    Raises:
        InvalidDocumentError: If python-pptx cannot open the file either.
    """
    from pptx import Presentation

    try:
        presentation = Presentation(io.BytesIO(data))
    except Exception as exc:
        raise InvalidDocumentError("The .pptx file could not be read.") from exc
    blocks = []
    for number, slide in enumerate(presentation.slides, start=1):
        title = slide.shapes.title.text.strip() if slide.shapes.title is not None else ""
        lines = []
        for shape in slide.shapes:
            if shape.has_text_frame and shape != slide.shapes.title:
                lines += [p.text.strip() for p in shape.text_frame.paragraphs if p.text.strip()]
            elif shape.has_table:
                lines += [" | ".join(c.text.strip() for c in row.cells) for row in shape.table.rows]
        if title or lines:
            path = (title,) if title else ()
            blocks.append(
                ExtractedBlock(text="\n".join(lines) or title, page=number, section_path=path)
            )
    return blocks


# --- Plain text, Markdown and FAQ records --------------------------------------


def extract_text_file(text: str) -> list[ExtractedBlock]:
    """Split TXT/MD text into blocks at Markdown headings.

    Markdown tables (lines starting with ``|``) become their own table blocks.

    Args:
        text: The decoded file.

    Returns:
        Blocks with ``page=None`` and the heading path as ``section_path``.
    """
    headings: list[tuple[int, str]] = []
    blocks: list[ExtractedBlock] = []
    lines: list[str] = []
    current_kind: BlockKind = "text"

    def flush() -> None:
        content = "\n".join(lines).strip()
        if content:
            blocks.append(
                ExtractedBlock(
                    text=content,
                    page=None,
                    kind=current_kind,
                    section_path=tuple(t for _, t in headings),
                )
            )
        lines.clear()

    for line in text.splitlines():
        heading = MARKDOWN_HEADING_RE.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, heading.group(2)))
            continue
        kind: BlockKind = "table" if line.lstrip().startswith("|") else "text"
        if kind != current_kind and line.strip():
            flush()
            current_kind = kind
        lines.append(line)
    flush()
    return blocks


def load_faq_records(path: str | Path) -> list[ExtractedBlock]:
    """Load the FAQ JSONL file as one block per question and answer.

    Used by the evaluation runner: JSONL is not an upload type. Each block's
    ``page`` is the FAQ number, which is how the golden set labels FAQ sources.

    Args:
        path: Path to ``faqs.jsonl``.

    Returns:
        One ``faq`` block per record.
    """
    blocks = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        number = record.get("faq_number")
        blocks.append(
            ExtractedBlock(
                text=record["text"], page=number, kind="faq", section_path=(f"FAQ {number}",)
            )
        )
    return blocks
