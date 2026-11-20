"""Upload validation: the first gate every file passes before it is stored.

Checks run on the raw bytes, cheapest first, so a bad file is rejected before
anything touches the disk, Neon or Qdrant:

1. size (413) while reading, so a huge upload is never fully held in memory;
2. empty (400);
3. extension and real content type by magic bytes (415), so a renamed .exe
   cannot pass as a PDF;
4. per type: corrupt or encrypted PDF, corrupt zip or zip bomb (400).
"""

import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePath
from typing import Literal, Protocol

import pymupdf

from app.config import settings
from app.exceptions import FileTooLargeError, InvalidDocumentError, UnsupportedFileTypeError

FileType = Literal["pdf", "docx", "pptx", "txt", "md"]
SUPPORTED_EXTENSIONS: tuple[FileType, ...] = ("pdf", "docx", "pptx", "txt", "md")

# Read uploads in 1 MB pieces: small enough to stop soon after the limit is
# crossed, big enough that a 25 MB file needs only 25 reads.
READ_CHUNK_BYTES = 1024 * 1024
# A PDF header must start within the first 1024 bytes (PDF spec, section 7.5.2).
PDF_HEADER_WINDOW = 1024
ZIP_MAGIC = b"PK\x03\x04"
# Old Office container, also used for password-protected DOCX/PPTX.
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class AsyncReadable(Protocol):
    """Anything with an async ``read(size)``, such as FastAPI's ``UploadFile``."""

    async def read(self, size: int = -1) -> bytes:
        """Read up to ``size`` bytes; an empty result means end of file."""
        ...


@dataclass(frozen=True)
class ValidatedFile:
    """An upload that passed every check, ready for extraction.

    Attributes:
        data: The file bytes.
        filename: Original file name (only the final part, no folders).
        file_type: Type confirmed from the content, not just the name.
        sha256: Hex hash of the bytes, used for duplicate detection.
        page_count: Pages (PDF) or slides (PPTX); None when the format has no
            fixed pages (DOCX, TXT, MD).
    """

    data: bytes
    filename: str
    file_type: FileType
    sha256: str
    page_count: int | None


async def read_limited(upload: AsyncReadable, max_bytes: int | None = None) -> bytes:
    """Read an upload, stopping as soon as it is bigger than the limit.

    Args:
        upload: The uploaded file.
        max_bytes: Size limit; defaults to ``settings.max_upload_mb``.

    Returns:
        The complete file bytes.

    Raises:
        FileTooLargeError: If the file is larger than the limit.
    """
    limit = max_bytes if max_bytes is not None else settings.max_upload_mb * 1024 * 1024
    buffer = bytearray()
    while piece := await upload.read(READ_CHUNK_BYTES):
        buffer.extend(piece)
        if len(buffer) > limit:
            raise FileTooLargeError(
                f"The file is larger than the {settings.max_upload_mb} MB upload limit."
            )
    return bytes(buffer)


def validate_file(data: bytes, filename: str) -> ValidatedFile:
    """Run every content check on an uploaded file.

    Args:
        data: The file bytes (already size-limited by `read_limited`).
        filename: The name the client sent.

    Returns:
        The validated file with its confirmed type, hash and page count.

    Raises:
        InvalidDocumentError: If the file is empty, corrupt, encrypted, a zip
            bomb, or contains no text.
        UnsupportedFileTypeError: If the extension is not supported or the
            content does not match it.
    """
    if not data:
        raise InvalidDocumentError("The file is empty.")
    # Keep only the last path part: a name like "../../x.pdf" must never
    # influence where anything is written later.
    safe_name = PurePath(filename.replace("\\", "/")).name or "upload"
    extension = _extension(safe_name)
    if extension in ("docx", "pptx") and data.startswith(OLE_MAGIC):
        # Office saves password-protected files in the old OLE container instead
        # of a zip; saying "encrypted" is more helpful than "unsupported type".
        raise InvalidDocumentError(
            f"The .{extension} file is password-protected or in the old Office "
            "format; save an unlocked .docx/.pptx copy."
        )
    detected = detect_type(data)
    if not _matches(extension, detected):
        raise UnsupportedFileTypeError(
            f"The file content does not match its .{extension} extension."
        )

    page_count: int | None = None
    if extension == "pdf":
        page_count = _check_pdf(data)
    elif extension in ("docx", "pptx"):
        page_count = _check_office_zip(data, extension)
    else:
        _check_text(data)

    return ValidatedFile(
        data=data,
        filename=safe_name,
        file_type=extension,
        sha256=hashlib.sha256(data).hexdigest(),
        page_count=page_count,
    )


def detect_type(data: bytes) -> Literal["pdf", "docx", "pptx", "text"] | None:
    """Guess the real file type from the bytes themselves (magic bytes).

    Args:
        data: The file bytes.

    Returns:
        ``pdf``, ``docx``, ``pptx`` or ``text``; None if it is none of these.
    """
    if b"%PDF-" in data[:PDF_HEADER_WINDOW]:
        return "pdf"
    if data.startswith(ZIP_MAGIC):
        # DOCX and PPTX are both zip files; the part names inside tell them apart.
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile:
            return None
        if "word/document.xml" in names:
            return "docx"
        if "ppt/presentation.xml" in names:
            return "pptx"
        return None
    if _is_utf8_text(data):
        return "text"
    return None


def _extension(filename: str) -> FileType:
    """Return the file's lower-case extension if it is a supported one.

    Args:
        filename: File name without folders.

    Returns:
        The extension, e.g. ``pdf``.

    Raises:
        UnsupportedFileTypeError: If the extension is missing or not supported.
    """
    extension = PurePath(filename).suffix.lower().lstrip(".")
    if extension not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileTypeError()
    return extension  # type: ignore[return-value]  # checked against the Literal above


def _matches(extension: FileType, detected: str | None) -> bool:
    """Say whether the detected content type fits the extension.

    Args:
        extension: Extension from the file name.
        detected: Result of `detect_type`.

    Returns:
        True if they agree (TXT and MD both count as plain text).
    """
    if extension in ("txt", "md"):
        return detected == "text"
    return detected == extension


def _check_pdf(data: bytes) -> int:
    """Make sure a PDF opens, is not password-protected and has pages.

    Args:
        data: PDF bytes.

    Returns:
        The page count.

    Raises:
        InvalidDocumentError: If the PDF is corrupt, encrypted or has no pages.
    """
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several types for broken files
        raise InvalidDocumentError("The PDF is corrupt and cannot be opened.") from exc
    with document:
        # needs_pass is true only when a password is required to READ the file;
        # PDFs that merely restrict printing or copying open fine and are allowed.
        if document.needs_pass:
            raise InvalidDocumentError("The PDF is password-protected; upload an unlocked copy.")
        if document.page_count == 0:
            raise InvalidDocumentError("The PDF has no pages.")
        return document.page_count


def _check_office_zip(data: bytes, extension: FileType) -> int | None:
    """Reject corrupt DOCX/PPTX files and zip bombs.

    The sizes written in a zip's directory can lie, so instead of trusting them
    every part is actually decompressed and counted, stopping at the limit. A
    damaged part fails its CRC check here too.

    Args:
        data: The zip bytes.
        extension: ``docx`` or ``pptx``.

    Returns:
        Slide count for PPTX; None for DOCX.

    Raises:
        InvalidDocumentError: If the zip is corrupt or expands too much.
    """
    max_total = settings.max_unzipped_mb * 1024 * 1024
    max_total = min(max_total, len(data) * settings.max_zip_ratio)
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                with archive.open(info) as part:
                    while piece := part.read(READ_CHUNK_BYTES):
                        total += len(piece)
                        if total > max_total:
                            raise InvalidDocumentError(
                                "The file expands to far more data than its size "
                                "suggests (possible zip bomb)."
                            )
            names = archive.namelist()
    # BadZipFile/EOFError: damaged data or CRC mismatch; NotImplementedError:
    # unknown compression method; RuntimeError: zip-level encryption.
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        EOFError,
        ValueError,
        NotImplementedError,
        RuntimeError,
    ) as exc:
        raise InvalidDocumentError(f"The .{extension} file is corrupt.") from exc

    if extension == "pptx":
        return sum(1 for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
    return None


def _check_text(data: bytes) -> None:
    """Make sure a TXT/MD file has some real text.

    Args:
        data: The file bytes (already known to be UTF-8).

    Raises:
        InvalidDocumentError: If the file holds only whitespace.
    """
    if not data.decode("utf-8-sig").strip():
        raise InvalidDocumentError("The file contains no text.")


def _is_utf8_text(data: bytes) -> bool:
    """Say whether bytes look like a UTF-8 text file.

    Args:
        data: The file bytes.

    Returns:
        True if the bytes decode as UTF-8 and contain no NUL bytes (binary
        formats such as .exe are full of NULs; real text never has them).
    """
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    return True
