"""Build small, real test files in memory (no binary fixtures in Git).

Shared by the validation unit tests and the /documents API tests.
"""

import io
import zipfile

import pymupdf
from docx import Document as DocxDocument
from pptx import Presentation

DEFAULT_PDF_TEXT = "Rule 150V. Extension of time. The Commissioner may extend the date."


def make_pdf(pages: list[str] | None = None, password: str | None = None) -> bytes:
    """Create a PDF with a text layer, one string per page.

    Args:
        pages: Text for each page; defaults to one page of rule text.
        password: If given, the PDF needs this password to open.

    Returns:
        The PDF bytes.
    """
    document = pymupdf.open()
    for text in pages or [DEFAULT_PDF_TEXT]:
        document.new_page().insert_textbox(pymupdf.Rect(72, 72, 540, 770), text)
    if password:
        data = document.tobytes(
            encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password
        )
    else:
        data = document.tobytes()
    document.close()
    return data


def make_docx(paragraphs: list[str] | None = None) -> bytes:
    """Create a DOCX with a heading and paragraphs.

    Args:
        paragraphs: Body paragraphs; defaults to one sentence.

    Returns:
        The DOCX bytes.
    """
    document = DocxDocument()
    document.add_heading("Digital Invoicing Notes", level=1)
    for text in paragraphs or ["Invoices must be reported to FBR in real time."]:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def make_pptx(slides: list[str] | None = None) -> bytes:
    """Create a PPTX with one titled slide per string.

    Args:
        slides: Title text for each slide; defaults to two slides.

    Returns:
        The PPTX bytes.
    """
    presentation = Presentation()
    layout = presentation.slide_layouts[1]  # "Title and Content"
    for title in slides or ["Integration steps", "Sandbox testing"]:
        slide = presentation.slides.add_slide(layout)
        slide.shapes.title.text = title
        slide.placeholders[1].text = f"Details about {title.lower()}."
    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def make_zip(parts: dict[str, bytes]) -> bytes:
    """Create a zip with the given part names and contents (deflate-compressed).

    Args:
        parts: Mapping of part name to its bytes.

    Returns:
        The zip bytes.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()
