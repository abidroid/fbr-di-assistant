"""Document logic: upload (the whole ingestion pipeline), list, get, delete.

Upload, in order:

1. read the upload (size limit) and validate it (type, corrupt, zip bomb);
2. refuse a duplicate: the same user uploading identical bytes gets 409
   with the existing document's id;
3. save the database row (status ``processing``) and the file on disk;
4. extract and chunk (seconds, always inside the request);
5. embed and store in Qdrant: now (201, ``ready``) when the document has at
   most ``SYNC_INGEST_MAX_CHUNKS`` chunks, otherwise in a background task
   (202, ``processing``) that sets ``ready`` or ``failed`` when done.

Delete removes the vectors first, then the file, then the row: if a step
fails the user can retry, and no vectors are ever left without a row.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import anyio
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.exceptions import (
    DocumentNotFoundError,
    DomainError,
    DuplicateDocumentError,
    InvalidDocumentError,
)
from app.ingestion.chunk import Chunk, chunk_blocks
from app.ingestion.embed import DenseEmbedder, SparseEmbedder
from app.ingestion.extract import Extractor
from app.ingestion.store import (
    DocumentMeta,
    delete_document_points,
    ensure_collection,
    upsert_chunks,
)
from app.ingestion.validate import AsyncReadable, ValidatedFile, read_limited, validate_file
from app.models import Document, DocumentStatus, User

logger = logging.getLogger(__name__)

# Starts a function after the response is sent (FastAPI's BackgroundTasks.add_task).
Scheduler = Callable[..., Any]


@dataclass(frozen=True)
class Pipeline:
    """The long-lived objects ingestion needs, created once in the lifespan.

    Attributes:
        extractor: Reads files into blocks.
        dense: Dense embedder (BGE-M3, or a fake in tests).
        sparse: Sparse embedder (BM25, or a fake in tests).
        qdrant: Vector database client.
    """

    extractor: Extractor
    dense: DenseEmbedder
    sparse: SparseEmbedder
    qdrant: AsyncQdrantClient


@dataclass(frozen=True)
class CorpusInfo:
    """What the corpus manifest says about a known file.

    Attributes:
        title: Official title, used in every chunk's context header.
        authority: "law", "official_guidance" or "faq".
        version_date: Date the text is valid for (ISO), if known.
        superseded: True for an outdated version.
    """

    title: str
    authority: str
    version_date: str | None
    superseded: bool


# --- Upload ------------------------------------------------------------------------


async def upload_document(
    session: AsyncSession,
    user: User,
    upload: AsyncReadable,
    filename: str,
    pipeline: Pipeline,
    schedule: Scheduler,
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[Document, bool]:
    """Validate, store and ingest an uploaded file.

    Args:
        session: Database session for this request.
        user: The uploader; the document is theirs only.
        upload: The uploaded file stream.
        filename: The name the client sent.
        pipeline: Extractor, embedders and Qdrant client.
        schedule: Runs a function after the response (background ingestion).
        session_factory: Opens a new session for the background task (the
            request's session is closed by then).

    Returns:
        The document, and True if ingestion finished (201) or False if it
        continues in the background (202).

    Raises:
        FileTooLargeError: Over the upload limit (413).
        UnsupportedFileTypeError: Not a supported type (415).
        InvalidDocumentError: Empty, corrupt, encrypted or no text (400).
        DuplicateDocumentError: The user already has this file (409).
        UpstreamUnavailableError: Qdrant or the caption model is down (503).
    """
    data = await read_limited(upload)
    # Unzipping a 25 MB DOCX to check for a zip bomb is CPU work.
    file = await anyio.to_thread.run_sync(validate_file, data, filename)
    # Without the lock, two identical uploads at the same instant both pass the
    # duplicate check. With it, the second waits until the first row is saved,
    # then sees it and gets 409.
    async with _upload_lock(user.id, file.sha256):
        await _refuse_duplicate(session, user, file.sha256)
        document = Document(
            owner_id=user.id,
            filename=file.filename,
            file_type=file.file_type,
            size_bytes=len(file.data),
            sha256=file.sha256,
            status=DocumentStatus.PROCESSING,
            pages=file.page_count,
        )
        session.add(document)
        try:
            await session.commit()
        except IntegrityError:
            # The lock only covers this process; with several API processes the
            # unique constraint is the backstop that stops the second upload.
            await session.rollback()
            await _refuse_duplicate(session, user, file.sha256)
            raise
    path = file_path(user.id, document.id, file.file_type)

    try:
        await _save_file(path, file.data)
        chunks, meta = await _prepare(document, file, pipeline)
    except Exception:
        # Nothing was stored in Qdrant yet: remove the row and file so the
        # failed upload leaves no trace and can simply be retried.
        await _discard(session, document, path)
        raise

    if len(chunks) > settings.sync_ingest_max_chunks:
        await session.commit()
        schedule(finish_in_background, document.id, chunks, meta, pipeline, session_factory)
        return document, False

    try:
        document.chunk_count = await embed_and_store(chunks, meta, pipeline)
    except Exception:
        try:
            await delete_document_points(pipeline.qdrant, document.id)
        except Exception:
            # Qdrant is probably the thing that failed; the row and file must
            # still go, or the user is blocked by a "duplicate" that never worked.
            logger.exception("Could not remove partial vectors of document %s", document.id)
        await _discard(session, document, path)
        raise
    document.status = DocumentStatus.READY
    await session.commit()
    await session.refresh(document)
    return document, True


async def _prepare(
    document: Document, file: ValidatedFile, pipeline: Pipeline
) -> tuple[list[Chunk], DocumentMeta]:
    """Extract and chunk a file, and record what was learned on the row.

    Args:
        document: The new row (pages, OCR pages and warnings are set here).
        file: The validated upload.
        pipeline: Holds the extractor.

    Returns:
        The chunks and the metadata copied into every chunk's payload.

    Raises:
        InvalidDocumentError: If the file yields no chunk with text.
    """
    extraction = await pipeline.extractor.extract(file)
    corpus = corpus_info(file.sha256)
    title = corpus.title if corpus else (extraction.title or readable_title(file.filename))
    chunks = await anyio.to_thread.run_sync(chunk_blocks, extraction.blocks, title)
    if not chunks:
        raise InvalidDocumentError("No readable text was found in the file.")

    warnings = list(extraction.warnings)
    suspicious = sum(chunk.suspicious for chunk in chunks)
    if suspicious:
        warnings.append(
            f"{suspicious} chunk(s) contain instruction-like text; they are marked suspicious "
            "and treated as data, never as instructions."
        )
    document.pages = extraction.page_count or document.pages
    document.ocr_pages = extraction.ocr_pages
    document.warnings = warnings

    meta = DocumentMeta(
        doc_id=document.id,
        owner_id=document.owner_id,
        filename=document.filename,
        authority=corpus.authority if corpus else "third_party",
        version_date=corpus.version_date if corpus else None,
        superseded=corpus.superseded if corpus else False,
    )
    return chunks, meta


async def embed_and_store(
    chunks: list[Chunk],
    meta: DocumentMeta,
    pipeline: Pipeline,
    collection: str | None = None,
) -> int:
    """Embed chunks (dense and sparse at the same time) and write them to Qdrant.

    Args:
        chunks: The document's chunks.
        meta: Payload facts about the document.
        pipeline: Embedders and Qdrant client.
        collection: Target collection; defaults to ``settings.qdrant_collection``.
            The evaluation runner passes its own per-config collection.

    Returns:
        Number of points stored.
    """
    texts = [chunk.embed_text for chunk in chunks]
    # Independent work: the two embedders run concurrently.
    dense, sparse = await asyncio.gather(
        pipeline.dense.embed_documents(texts), pipeline.sparse.embed_documents(texts)
    )
    await ensure_collection(pipeline.qdrant, collection)
    return await upsert_chunks(pipeline.qdrant, chunks, dense, sparse, meta, collection)


async def finish_in_background(
    doc_id: uuid.UUID,
    chunks: list[Chunk],
    meta: DocumentMeta,
    pipeline: Pipeline,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Embed and store a large document after the 202 response was sent.

    Never raises: there is no client left to answer. Failure is recorded on
    the row (``failed`` + ``error``) and partial vectors are removed.

    Args:
        doc_id: The document being ingested.
        chunks: Its chunks, prepared during the request.
        meta: Payload facts about it.
        pipeline: Embedders and Qdrant client.
        session_factory: Opens this task's own database session.
    """
    try:
        count = await embed_and_store(chunks, meta, pipeline)
    except Exception as exc:
        logger.exception("Background ingestion failed for document %s", doc_id)
        try:
            await delete_document_points(pipeline.qdrant, doc_id)
        except Exception:
            logger.exception("Could not remove partial vectors of document %s", doc_id)
        message = exc.message if isinstance(exc, DomainError) else "Ingestion failed."
        await _set_status(session_factory, doc_id, DocumentStatus.FAILED, error=message)
        return
    found = await _set_status(session_factory, doc_id, DocumentStatus.READY, chunk_count=count)
    if not found:
        # The user deleted the document while it was being embedded; its row
        # is gone, so the vectors just written must go too.
        await delete_document_points(pipeline.qdrant, doc_id)


async def _set_status(
    session_factory: async_sessionmaker[AsyncSession],
    doc_id: uuid.UUID,
    status: DocumentStatus,
    *,
    chunk_count: int | None = None,
    error: str | None = None,
) -> bool:
    """Record the result of background ingestion on the row.

    Args:
        session_factory: Opens a session.
        doc_id: The document.
        status: ``ready`` or ``failed``.
        chunk_count: Points stored (on success).
        error: Reason (on failure).

    Returns:
        False if the row no longer exists (deleted while processing).
    """
    async with session_factory() as session:
        document = await session.get(Document, doc_id)
        if document is None:
            return False
        document.status = status
        document.error = error
        if chunk_count is not None:
            document.chunk_count = chunk_count
        await session.commit()
        return True


@dataclass
class _KeyLock:
    """A lock plus how many uploads hold or wait for it (to know when to drop it)."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


# One entry per (owner, file hash) being uploaded right now; removed when free,
# so the dict never grows with the number of past uploads.
_upload_locks: dict[tuple[uuid.UUID, str], _KeyLock] = {}


@asynccontextmanager
async def _upload_lock(owner_id: uuid.UUID, sha256: str) -> AsyncIterator[None]:
    """Let only one upload of the same file by the same user run the duplicate check.

    Args:
        owner_id: The uploader.
        sha256: Hash of the file.

    Yields:
        Control while the lock is held.
    """
    key = (owner_id, sha256)
    entry = _upload_locks.setdefault(key, _KeyLock())
    entry.users += 1
    try:
        async with entry.lock:
            yield
    finally:
        entry.users -= 1
        if entry.users == 0:
            del _upload_locks[key]


async def _refuse_duplicate(session: AsyncSession, user: User, sha256: str) -> None:
    """Raise if this user already has a file with the same bytes.

    A previous attempt that ``failed`` does not count: it is removed so the
    user can simply upload the file again.

    Args:
        session: Database session.
        user: The uploader.
        sha256: Hash of the new file.

    Raises:
        DuplicateDocumentError: With the existing document's id.
    """
    existing = await session.scalar(
        select(Document).where(Document.owner_id == user.id, Document.sha256 == sha256)
    )
    if existing is None:
        return
    if existing.status == DocumentStatus.FAILED:
        await _discard(session, existing, file_path(user.id, existing.id, existing.file_type))
        return
    raise DuplicateDocumentError(existing.id)


# --- List, get, delete -------------------------------------------------------------


async def list_documents(session: AsyncSession, user: User) -> list[Document]:
    """Return the user's documents, newest first.

    Args:
        session: Database session.
        user: The owner.

    Returns:
        Only this user's documents.
    """
    result = await session.scalars(
        select(Document).where(Document.owner_id == user.id).order_by(Document.created_at.desc())
    )
    return list(result)


async def get_document(session: AsyncSession, user: User, doc_id: uuid.UUID) -> Document:
    """Return one of the user's documents.

    Args:
        session: Database session.
        user: Who is asking.
        doc_id: The document.

    Returns:
        The document.

    Raises:
        DocumentNotFoundError: If it does not exist or belongs to someone else;
            both give 404 so nobody can probe for other users' ids.
    """
    document = await session.get(Document, doc_id)
    if document is None or document.owner_id != user.id:
        raise DocumentNotFoundError()
    return document


async def delete_document(
    session: AsyncSession, user: User, doc_id: uuid.UUID, qdrant: AsyncQdrantClient
) -> None:
    """Delete a document: its vectors, its file, then its row.

    Args:
        session: Database session.
        user: Who is asking.
        doc_id: The document.
        qdrant: Vector database client.

    Raises:
        DocumentNotFoundError: If it does not exist or belongs to someone else.
        UpstreamUnavailableError: If Qdrant is down (nothing else is deleted then).
    """
    document = await get_document(session, user, doc_id)
    # Vectors first: if this fails, the row stays and the user can retry,
    # so searchable vectors can never outlive their document.
    await delete_document_points(qdrant, document.id, user.id)
    await _discard(session, document, file_path(user.id, document.id, document.file_type))


# --- Files and metadata ---------------------------------------------------------------


def file_path(owner_id: uuid.UUID, doc_id: uuid.UUID, file_type: str) -> Path:
    """Return where a document's file is kept: UPLOAD_DIR/<owner>/<doc>.<ext>.

    The name is built from ids only, never from the user's file name, so a
    name like "../../x.pdf" cannot write outside the upload folder.

    Args:
        owner_id: The owner.
        doc_id: The document.
        file_type: Its extension.

    Returns:
        The file path.
    """
    return Path(settings.upload_dir) / str(owner_id) / f"{doc_id}.{file_type}"


async def _save_file(path: Path, data: bytes) -> None:
    """Write the uploaded bytes, creating the owner's folder if needed."""
    target = anyio.Path(path)
    await target.parent.mkdir(parents=True, exist_ok=True)
    await target.write_bytes(data)


async def _discard(session: AsyncSession, document: Document, path: Path) -> None:
    """Delete a document's file (if any) and its row.

    Args:
        session: Database session.
        document: The row to delete.
        path: Its file on disk.
    """
    await anyio.Path(path).unlink(missing_ok=True)
    await session.delete(document)
    await session.commit()


def readable_title(filename: str) -> str:
    """Turn a file name into a title for the context header.

    Args:
        filename: E.g. "di_user_manual_v1.5.pdf".

    Returns:
        E.g. "di user manual v1.5".
    """
    return Path(filename).stem.replace("_", " ").strip() or filename


def corpus_info(sha256: str) -> CorpusInfo | None:
    """Look up a file in the corpus manifest by its hash.

    Args:
        sha256: Hash of the uploaded bytes.

    Returns:
        The manifest's facts, or None for any other file.
    """
    return _load_manifest(settings.corpus_manifest_path).get(sha256)


@lru_cache
def _load_manifest(path: str) -> dict[str, CorpusInfo]:
    """Read the manifest once (it only changes with a new deployment).

    Args:
        path: Path to ``manifest.jsonl``.

    Returns:
        sha256 -> facts; empty if the file is missing.
    """
    manifest = Path(path)
    if not manifest.exists():
        logger.warning("Corpus manifest %s not found; uploads count as third-party", path)
        return {}
    records = [json.loads(line) for line in manifest.read_text("utf-8").splitlines() if line]
    return {
        record["sha256"]: CorpusInfo(
            title=record["title"],
            authority=record.get("authority", "third_party"),
            version_date=record.get("version_date"),
            superseded=bool(record.get("superseded", False)),
        )
        for record in records
        if record.get("sha256")
    }
