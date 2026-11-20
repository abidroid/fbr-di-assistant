"""Unit tests for app.ingestion.validate: every rule, success and failure."""

import io
import os

import pytest
from fastapi import UploadFile

from app.config import settings
from app.exceptions import FileTooLargeError, InvalidDocumentError, UnsupportedFileTypeError
from app.ingestion.validate import detect_type, read_limited, validate_file
from tests.file_factories import make_docx, make_pdf, make_pptx, make_zip

# --- read_limited: size limit (413) -------------------------------------------


async def test_read_limited_returns_all_bytes_under_limit() -> None:
    upload = UploadFile(file=io.BytesIO(b"x" * 100), filename="a.txt")
    assert await read_limited(upload, max_bytes=100) == b"x" * 100


async def test_read_limited_rejects_file_over_limit() -> None:
    upload = UploadFile(file=io.BytesIO(b"x" * 101), filename="a.txt")
    with pytest.raises(FileTooLargeError):
        await read_limited(upload, max_bytes=100)


# --- Accepted files -----------------------------------------------------------


def test_valid_pdf_returns_type_hash_and_pages() -> None:
    data = make_pdf(["page one", "page two"])
    result = validate_file(data, "rules.pdf")
    assert result.file_type == "pdf"
    assert result.page_count == 2
    assert len(result.sha256) == 64


def test_valid_docx_has_no_page_count() -> None:
    result = validate_file(make_docx(), "notes.docx")
    assert result.file_type == "docx"
    assert result.page_count is None


def test_valid_pptx_counts_slides() -> None:
    result = validate_file(make_pptx(["a", "b", "c"]), "deck.pptx")
    assert result.file_type == "pptx"
    assert result.page_count == 3


@pytest.mark.parametrize("name", ["readme.md", "notes.txt", "UPPER.TXT"])
def test_text_files_are_accepted(name: str) -> None:
    result = validate_file("# Title\nسیلز ٹیکس".encode(), name)
    assert result.file_type == name.rsplit(".", 1)[1].lower()


def test_text_with_utf8_bom_is_accepted() -> None:
    assert validate_file(b"\xef\xbb\xbfhello", "bom.txt").file_type == "txt"


def test_folder_parts_are_stripped_from_filename() -> None:
    result = validate_file(make_pdf(), "..\\..\\secret/../rules.pdf")
    assert result.filename == "rules.pdf"


def test_same_bytes_give_same_hash() -> None:
    data = make_pdf()
    assert validate_file(data, "a.pdf").sha256 == validate_file(data, "b.pdf").sha256


# --- Empty and no text (400) --------------------------------------------------


def test_empty_file_is_rejected() -> None:
    with pytest.raises(InvalidDocumentError, match="empty"):
        validate_file(b"", "empty.pdf")


def test_whitespace_only_text_is_rejected() -> None:
    with pytest.raises(InvalidDocumentError, match="no text"):
        validate_file(b"  \n\t ", "blank.md")


# --- Type checks (415) --------------------------------------------------------


@pytest.mark.parametrize("name", ["malware.exe", "data.jsonl", "noextension", "old.doc"])
def test_unsupported_extension_is_rejected(name: str) -> None:
    with pytest.raises(UnsupportedFileTypeError):
        validate_file(b"some content", name)


def test_exe_renamed_to_pdf_is_rejected() -> None:
    fake_exe = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 100
    with pytest.raises(UnsupportedFileTypeError, match="does not match"):
        validate_file(fake_exe, "invoice.pdf")


def test_pdf_renamed_to_docx_is_rejected() -> None:
    with pytest.raises(UnsupportedFileTypeError):
        validate_file(make_pdf(), "notes.docx")


def test_docx_renamed_to_pptx_is_rejected() -> None:
    with pytest.raises(UnsupportedFileTypeError):
        validate_file(make_docx(), "deck.pptx")


def test_binary_renamed_to_txt_is_rejected() -> None:
    with pytest.raises(UnsupportedFileTypeError):
        validate_file(b"\x00\x01\x02binary", "notes.txt")


def test_plain_zip_is_not_detected_as_office() -> None:
    assert detect_type(make_zip({"hello.txt": b"hi"})) is None


# --- Corrupt, encrypted, malicious (400) ---------------------------------------


def test_encrypted_pdf_is_rejected() -> None:
    with pytest.raises(InvalidDocumentError, match="password"):
        validate_file(make_pdf(password="secret"), "locked.pdf")


def test_corrupt_pdf_is_rejected() -> None:
    broken = b"%PDF-1.7\n" + os.urandom(2000)
    with pytest.raises(InvalidDocumentError, match="corrupt"):
        validate_file(broken, "broken.pdf")


def test_password_protected_office_file_is_rejected() -> None:
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512
    with pytest.raises(InvalidDocumentError, match="password"):
        validate_file(ole, "locked.docx")


def test_truncated_docx_is_rejected() -> None:
    data = make_docx()
    with pytest.raises((InvalidDocumentError, UnsupportedFileTypeError)):
        validate_file(data[: len(data) // 2], "cut.docx")


def test_zip_bomb_by_ratio_is_rejected() -> None:
    # 20 MB of zeros compresses to ~20 KB: a ratio near 1000, far above 100.
    bomb = make_zip({"word/document.xml": b"\x00" * (20 * 1024 * 1024)})
    with pytest.raises(InvalidDocumentError, match="zip bomb"):
        validate_file(bomb, "bomb.docx")


def test_zip_over_total_size_limit_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # Random bytes do not compress (ratio ~1), so only the size limit can catch it.
    monkeypatch.setattr(settings, "max_unzipped_mb", 1)
    big = make_zip({"word/document.xml": b"<w/>", "word/media/big.bin": os.urandom(2 * 1024**2)})
    with pytest.raises(InvalidDocumentError, match="zip bomb"):
        validate_file(big, "big.docx")
