"""Unit tests for app.ingestion.extract (no real models, no network).

PDF layouts are drawn with PyMuPDF so each clean-up rule can be shown in
isolation: running headers, footnote markers, amendments, headings, tables,
and pages that need captions or OCR.
"""

import json
from pathlib import Path

import pymupdf
import pytest

from app.exceptions import InvalidDocumentError
from app.ingestion.caption import FakeCaptioner
from app.ingestion.extract import (
    Extractor,
    amendment_refs,
    extract_text_file,
    load_faq_records,
    normalise_line,
    read_pdf_text_layer,
    read_pptx_fallback,
)
from app.ingestion.validate import validate_file
from tests.file_factories import make_docx, make_pdf, make_pptx

BODY = (
    "The integrated person shall issue a real-time verifiable electronic sales tax invoice "
    "for every taxable supply made to any person."
)


def pdf_bytes(document: pymupdf.Document) -> bytes:
    """Serialise and close a PyMuPDF document."""
    data = document.tobytes()
    document.close()
    return data


async def extract(data: bytes, name: str) -> tuple[list, object, FakeCaptioner]:
    """Validate and extract a file with a fake captioner."""
    captioner = FakeCaptioner()
    result = await Extractor(captioner).extract(validate_file(data, name))
    return result.blocks, result, captioner


# --- Pure helpers ----------------------------------------------------------------


def test_normalise_line_ignores_page_numbers() -> None:
    assert normalise_line("(88)") == normalise_line("(91)")
    assert normalise_line("Page 6 of 51") == "page # of #"


def test_amendment_refs_finds_sro_and_finance_act() -> None:
    note = "Substituted by S.R.O. 69(I)/2025, dated 29th January, 2025 and Finance Act, 2024."
    assert amendment_refs(note) == ["S.R.O. 69(I)/2025", "Finance Act, 2024"]


def test_amendment_refs_falls_back_to_note_start() -> None:
    assert amendment_refs("Omitted vide notification of the Board.") == [
        "Omitted vide notification of the Board."
    ]


# --- PDF text layer ----------------------------------------------------------------


async def test_pdf_blocks_keep_page_numbers() -> None:
    blocks, result, _ = await extract(make_pdf([BODY, "Second page. " + BODY]), "rules.pdf")
    assert [b.page for b in blocks] == [1, 2]
    assert result.page_count == 2
    assert result.ocr_pages == 0


def test_running_header_and_page_numbers_are_removed() -> None:
    document = pymupdf.open()
    for number in range(1, 5):
        page = document.new_page()  # A4: 842 pt tall, so the bottom 8% starts at y=775
        page.insert_text((72, 30), "Sales Tax Rules, 2006", fontsize=10)
        page.insert_text((290, 825), f"({number + 80})", fontsize=11)
        page.insert_textbox(pymupdf.Rect(72, 100, 540, 300), f"Rule {number}. {BODY}")
    scan = read_pdf_text_layer(pdf_bytes(document))
    text = " ".join(b.text for b in scan.blocks)
    assert "Sales Tax Rules, 2006" not in text
    assert "(81)" not in text and "(84)" not in text
    assert "Rule 3." in text


def test_footnote_marker_removed_and_amendment_moved_to_metadata() -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 100), "150Q. Application.", fontsize=12)
    page.insert_text((72, 120), "252", fontsize=7)  # superscript marker
    page.insert_text(
        (90, 120), "[(3) Retailers whose withholding tax exceeds the limit.]", fontsize=12
    )
    page.insert_text((72, 700), "252", fontsize=5)
    page.insert_text(
        (85, 700), "Added by Notification No. S.R.O. 2071(I)/2025 dated 3rd November.", fontsize=8
    )
    blocks = read_pdf_text_layer(pdf_bytes(document)).blocks
    body = next(b for b in blocks if "Retailers" in b.text)
    assert "252" not in body.text
    assert body.amended_by == ("S.R.O. 2071(I)/2025",)
    assert not any("Added by" in b.text for b in blocks)


def test_non_amendment_footnote_is_kept_as_text() -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 100), "236", fontsize=7)
    page.insert_text(
        (90, 100), "[Officer of Sales Tax] authorized to receive the same.", fontsize=12
    )
    # A normal amount of body text, so 12 pt is recognised as the body size.
    page.insert_textbox(pymupdf.Rect(72, 120, 540, 400), BODY * 2, fontsize=12)
    page.insert_text((72, 700), "236", fontsize=5)
    page.insert_text(
        (85, 700), "Any reference to an Officer of Sales Tax means Officer of IR.", fontsize=8
    )
    blocks = read_pdf_text_layer(pdf_bytes(document)).blocks
    assert "[Officer of Sales Tax]" in blocks[0].text
    assert "236" not in blocks[0].text  # would otherwise sit next to "236G" queries
    assert any(b.text.startswith("Footnotes:\n[236] Any reference") for b in blocks)


def test_recurring_big_headings_become_section_path() -> None:
    document = pymupdf.open()
    for chapter in ("CHAPTER XIII", "CHAPTER XIV", "CHAPTER XV"):
        page = document.new_page()
        page.insert_text((72, 80), chapter, fontsize=14)
        page.insert_textbox(pymupdf.Rect(72, 100, 540, 300), BODY)
    blocks = read_pdf_text_layer(pdf_bytes(document)).blocks
    assert [b.section_path for b in blocks] == [
        ("CHAPTER XIII",),
        ("CHAPTER XIV",),
        ("CHAPTER XV",),
    ]


def test_one_off_cover_title_is_not_a_heading() -> None:
    document = pymupdf.open()
    cover = document.new_page()
    cover.insert_text((72, 80), "The SALES TAX RULES, 2006", fontsize=28)
    for chapter in ("CHAPTER I", "CHAPTER II"):
        page = document.new_page()
        page.insert_text((72, 80), chapter, fontsize=14)
        page.insert_textbox(pymupdf.Rect(72, 100, 540, 300), BODY)
    blocks = read_pdf_text_layer(pdf_bytes(document)).blocks
    assert blocks[-1].section_path == ("CHAPTER II",)


def test_numbered_bold_headings_nest_by_depth() -> None:
    document = pymupdf.open()
    page = document.new_page()
    y = 80
    for text, font in [
        ("4. Digital Invoicing API", "hebo"),
        (BODY[:80], "helv"),
        ("4.1. Web method - Post", "hebo"),
        (BODY[:80], "helv"),
        ("5. Reference APIs", "hebo"),
        (BODY[:80], "helv"),
    ]:
        page.insert_text((72, y), text, fontsize=11, fontname=font)
        y += 40
    blocks = read_pdf_text_layer(pdf_bytes(document)).blocks
    assert [b.section_path for b in blocks] == [
        ("4. Digital Invoicing API",),
        ("4. Digital Invoicing API", "4.1. Web method - Post"),
        ("5. Reference APIs",),
    ]


def test_legal_rule_title_is_not_a_heading() -> None:
    document = pymupdf.open()
    page = document.new_page()
    # Built-in Helvetica has no en dash (it would print "·"); a hyphen is
    # one of the dashes the rule check accepts.
    page.insert_text(
        (72, 80),
        "96. Attachment of movable property. - (1) Where it is",
        fontsize=12,
        fontname="hebo",
    )
    page.insert_text((72, 100), BODY[:90], fontsize=12)
    blocks = read_pdf_text_layer(pdf_bytes(document)).blocks
    assert blocks[0].section_path == ()
    assert "96. Attachment" in blocks[0].text


def test_ruled_table_becomes_markdown_table_block() -> None:
    document = pymupdf.open()
    page = document.new_page()
    rows = [("S No", "Status Code", "Message"), ("1.", "200", "Ok"), ("2.", "401", "Unauthorized")]
    xs, top, row_h = [72, 150, 300, 500], 100, 25
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            page.insert_text((xs[j] + 4, top + i * row_h + 17), cell, fontsize=11)
    for i in range(len(rows) + 1):
        page.draw_line((xs[0], top + i * row_h), (xs[-1], top + i * row_h))
    for x in xs:
        page.draw_line((x, top), (x, top + len(rows) * row_h))
    page.insert_textbox(pymupdf.Rect(72, 300, 540, 400), BODY)
    blocks = read_pdf_text_layer(pdf_bytes(document)).blocks
    table = next(b for b in blocks if b.kind == "table")
    # Exact column layout is PyMuPDF's job; we check the cells landed in a table.
    assert table.text.startswith("|S No|")
    assert all(cell in table.text for cell in ("Status Code", "401", "Unauthorized"))
    # The cell text must not be repeated as ordinary text.
    assert not any("Unauthorized" in b.text for b in blocks if b.kind == "text")


async def test_image_only_page_is_captioned() -> None:
    document = pymupdf.open()
    page = document.new_page()
    picture = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 80), False)
    picture.clear_with(200)
    page.insert_image(page.rect, pixmap=picture)
    blocks, result, captioner = await extract(pdf_bytes(document), "iris.pdf")
    assert captioner.calls == [(1, "iris.pdf")]
    assert blocks[0].kind == "caption" and blocks[0].ocr and blocks[0].page == 1
    assert result.ocr_pages == 1


async def test_page_without_text_or_images_goes_to_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    document = pymupdf.open()
    document.new_page().insert_textbox(pymupdf.Rect(72, 72, 540, 300), BODY)
    document.new_page()  # blank: no text layer, no image
    asked: list[list[int]] = []
    # Real Docling OCR loads ML models, which tests must not do.
    monkeypatch.setattr(
        Extractor, "_ocr_pages", lambda self, data, pages: asked.append(pages) or []
    )
    blocks, result, _ = await extract(pdf_bytes(document), "scan.pdf")
    assert asked == [[2]]
    assert result.warnings == ["Page 2: no text found, even with OCR."]
    assert [b.page for b in blocks] == [1]


# --- Office files (real Docling; no ML models needed for these formats) ---------


async def test_docx_keeps_heading_as_section_path() -> None:
    blocks, result, _ = await extract(make_docx(["First para.", "Second para."]), "notes.docx")
    assert blocks[0].section_path == ("Digital Invoicing Notes",)
    assert "First para." in blocks[0].text and "Second para." in blocks[0].text
    assert blocks[0].page is None and result.page_count is None


async def test_pptx_uses_slide_numbers_and_titles() -> None:
    blocks, result, _ = await extract(
        make_pptx(["Integration steps", "Sandbox testing"]), "deck.pptx"
    )
    assert [(b.page, b.section_path) for b in blocks] == [
        (1, ("Integration steps",)),
        (2, ("Sandbox testing",)),
    ]
    assert result.page_count == 2


def test_pptx_fallback_reads_titles_and_text() -> None:
    blocks = read_pptx_fallback(make_pptx(["Integration steps"]))
    assert blocks[0].page == 1
    assert blocks[0].section_path == ("Integration steps",)
    assert "Details about integration steps." in blocks[0].text


def test_pptx_fallback_rejects_garbage() -> None:
    with pytest.raises(InvalidDocumentError):
        read_pptx_fallback(b"not a pptx")


# --- Text, Markdown and FAQs -----------------------------------------------------


def test_markdown_headings_build_nested_paths() -> None:
    text = "# Guide\nIntro.\n## Sandbox\nTest first.\n## Production\nGo live.\n# Annex\nMore."
    blocks = extract_text_file(text)
    assert [(b.section_path, b.text) for b in blocks] == [
        (("Guide",), "Intro."),
        (("Guide", "Sandbox"), "Test first."),
        (("Guide", "Production"), "Go live."),
        (("Annex",), "More."),
    ]


def test_markdown_table_is_its_own_block() -> None:
    text = "# Codes\nSee below.\n| Code | Meaning |\n|---|---|\n| 0052 | Invalid HS code |\nAfter."
    kinds = [(b.kind, b.text.splitlines()[0]) for b in extract_text_file(text)]
    assert kinds == [("text", "See below."), ("table", "| Code | Meaning |"), ("text", "After.")]


async def test_file_with_only_headings_has_no_readable_text() -> None:
    with pytest.raises(InvalidDocumentError, match="No readable text"):
        await extract(b"# Title\n## Section\n", "empty.md")


def test_faq_records_use_faq_number_as_page(tmp_path: Path) -> None:
    path = tmp_path / "faqs.jsonl"
    records = [
        {"faq_number": 16, "text": "Q: What if we miss the deadline?\nA: Penalties."},
        {"faq_number": 17, "text": "Q: Who must integrate?\nA: Notified persons."},
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n\n", encoding="utf-8")
    blocks = load_faq_records(path)
    assert [(b.page, b.kind, b.section_path) for b in blocks] == [
        (16, "faq", ("FAQ 16",)),
        (17, "faq", ("FAQ 17",)),
    ]
