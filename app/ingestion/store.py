"""Qdrant storage: the collection, writing chunks, deleting a document.

One collection holds every user's chunks. Each point has two named vectors,
``dense`` (BGE-M3, cosine) and ``bm25`` (sparse, IDF weighting done by
Qdrant), plus a payload with everything a citation needs. Payload indexes on
``owner_id``, ``doc_id`` and ``superseded`` keep filtered search and delete
fast: without them Qdrant would scan every point.

Every call has the client's timeout plus retries with exponential backoff
for transient failures; after the last retry it raises
`UpstreamUnavailableError` (503).
"""

import hashlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.exceptions import UpstreamUnavailableError
from app.ingestion.chunk import Chunk
from app.ingestion.embed import SparseVector

logger = logging.getLogger(__name__)

DENSE_VECTOR = "dense"
SPARSE_VECTOR = "bm25"
# Points per upsert request: large enough to be fast, small enough that one
# request stays well under Qdrant's request size limit.
UPSERT_BATCH = 128
# uuid5 namespace for point ids: uuid5(namespace, "doc_id:index") is the same
# every time, so re-ingesting a document overwrites its points.
POINT_NAMESPACE = uuid.UUID("6f1c2a48-8f0e-4c5b-9d7e-2b8e1f0a9c31")


@dataclass(frozen=True)
class DocumentMeta:
    """Document-level facts copied into every chunk's payload.

    Attributes:
        doc_id: The document's id (also its Neon row id).
        owner_id: The user who uploaded it; every search filters on it.
        filename: Shown in citations.
        authority: "law", "official_guidance", "faq" or "third_party";
            retrieval ranks law above guidance.
        version_date: Date the text is valid for, if known (ISO string).
        superseded: True for an outdated version kept for comparison.
    """

    doc_id: uuid.UUID
    owner_id: uuid.UUID
    filename: str
    authority: str = "third_party"
    version_date: str | None = None
    superseded: bool = False


def _is_transient(error: BaseException) -> bool:
    """Say whether a Qdrant error is worth retrying.

    Connection problems, timeouts and 5xx answers can pass; a 4xx means our
    request is wrong and would fail again.

    Args:
        error: The raised exception.

    Returns:
        True for errors a retry might fix.
    """
    if isinstance(error, UnexpectedResponse):
        return error.status_code is not None and error.status_code >= 500
    return isinstance(
        error, ResponseHandlingException | httpx.TransportError | ConnectionError | TimeoutError
    )


async def with_retries[T](operation: Callable[[], Awaitable[T]], what: str) -> T:
    """Run a Qdrant call with retries and exponential backoff.

    Args:
        operation: A function that starts the call (called once per attempt).
        what: Short description for the log, e.g. "upsert".

    Returns:
        The call's result.

    Raises:
        UpstreamUnavailableError: If it still fails after the last attempt.
    """
    retrying = AsyncRetrying(
        retry=retry_if_exception(_is_transient),
        stop=stop_after_attempt(settings.qdrant_max_retries),
        wait=wait_exponential(multiplier=0.5, max=8),
        reraise=False,
    )
    try:
        async for attempt in retrying:
            with attempt:
                return await operation()
    except RetryError as exc:
        logger.warning("Qdrant %s failed after %d attempts", what, settings.qdrant_max_retries)
        raise UpstreamUnavailableError() from exc
    except Exception as exc:
        if _is_transient(exc):
            raise UpstreamUnavailableError() from exc
        raise
    raise AssertionError("unreachable: tenacity either returns or raises")  # pragma: no cover


async def ensure_collection(client: AsyncQdrantClient, name: str | None = None) -> None:
    """Create the collection and its payload indexes if they do not exist yet.

    Safe to call on every upload: it does nothing when the collection is there.

    Args:
        client: The Qdrant client.
        name: Collection name; defaults to ``settings.qdrant_collection``.
    """
    collection = name or settings.qdrant_collection
    if await with_retries(lambda: client.collection_exists(collection), "collection_exists"):
        return
    hnsw = models.HnswConfigDiff(m=settings.hnsw_m, ef_construct=settings.hnsw_ef_construct)
    try:
        await with_retries(
            lambda: client.create_collection(
                collection,
                vectors_config={
                    DENSE_VECTOR: models.VectorParams(
                        size=settings.embedding_dim, distance=models.Distance.COSINE
                    )
                },
                # IDF ("how rare is this word") is computed by Qdrant from the
                # whole collection, so new documents update it automatically.
                sparse_vectors_config={
                    SPARSE_VECTOR: models.SparseVectorParams(modifier=models.Modifier.IDF)
                },
                hnsw_config=hnsw,
            ),
            "create_collection",
        )
    except UnexpectedResponse as exc:
        # Two first uploads at the same moment: the other one created it.
        if exc.status_code != 409:
            raise
    for field, schema in (
        ("owner_id", models.PayloadSchemaType.KEYWORD),
        ("doc_id", models.PayloadSchemaType.KEYWORD),
        ("superseded", models.PayloadSchemaType.BOOL),
    ):
        await with_retries(
            lambda field=field, schema=schema: client.create_payload_index(
                collection, field_name=field, field_schema=schema
            ),
            "create_payload_index",
        )
    logger.info("Created Qdrant collection %s", collection)


def point_id(doc_id: uuid.UUID, chunk_index: int) -> str:
    """Return the stable point id of a chunk.

    Args:
        doc_id: Document id.
        chunk_index: Position of the chunk in the document.

    Returns:
        A UUID string, the same for the same document and position.
    """
    return str(uuid.uuid5(POINT_NAMESPACE, f"{doc_id}:{chunk_index}"))


def chunk_payload(chunk: Chunk, meta: DocumentMeta) -> dict[str, Any]:
    """Build the payload stored with a chunk (the fields listed in CLAUDE.md).

    Args:
        chunk: The chunk.
        meta: Facts about its document.

    Returns:
        A JSON-serialisable dict.
    """
    return {
        "chunk_id": point_id(meta.doc_id, chunk.chunk_index),
        "chunk_index": chunk.chunk_index,
        "owner_id": str(meta.owner_id),
        "doc_id": str(meta.doc_id),
        "filename": meta.filename,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "section_path": list(chunk.section_path),
        "chunk_type": chunk.chunk_type,
        "authority": meta.authority,
        "version_date": meta.version_date,
        "superseded": meta.superseded,
        "amended_by": list(chunk.amended_by),
        "ocr": chunk.ocr,
        "suspicious": chunk.suspicious,
        # Lets later tooling spot identical chunks across documents.
        "content_hash": hashlib.sha256(chunk.text.encode()).hexdigest(),
        "text": chunk.text,
    }


async def upsert_chunks(
    client: AsyncQdrantClient,
    chunks: list[Chunk],
    dense: list[list[float]],
    sparse: list[SparseVector],
    meta: DocumentMeta,
    name: str | None = None,
) -> int:
    """Write a document's chunks with both vectors and their payload.

    Args:
        client: The Qdrant client.
        chunks: The chunks, in order.
        dense: One dense vector per chunk.
        sparse: One sparse vector per chunk.
        meta: Facts about the document.
        name: Collection name; defaults to ``settings.qdrant_collection``.

    Returns:
        Number of points written.

    Raises:
        ValueError: If the vector lists do not match the chunks.
    """
    if not len(chunks) == len(dense) == len(sparse):
        raise ValueError("Each chunk needs exactly one dense and one sparse vector.")
    collection = name or settings.qdrant_collection
    points = [
        models.PointStruct(
            id=point_id(meta.doc_id, chunk.chunk_index),
            vector={
                DENSE_VECTOR: dense_vector,
                SPARSE_VECTOR: models.SparseVector(
                    indices=sparse_vector.indices, values=sparse_vector.values
                ),
            },
            payload=chunk_payload(chunk, meta),
        )
        for chunk, dense_vector, sparse_vector in zip(chunks, dense, sparse, strict=True)
    ]
    for start in range(0, len(points), UPSERT_BATCH):
        batch = points[start : start + UPSERT_BATCH]
        # wait=True: return only once the points are searchable, so a 201
        # response really means "ingested".
        await with_retries(
            lambda batch=batch: client.upsert(collection, points=batch, wait=True), "upsert"
        )
    return len(points)


def document_filter(doc_id: uuid.UUID, owner_id: uuid.UUID | None = None) -> models.Filter:
    """Build the filter that selects one document's points.

    Args:
        doc_id: The document.
        owner_id: Also require this owner (defence in depth for deletes).

    Returns:
        A Qdrant filter.
    """
    conditions = [models.FieldCondition(key="doc_id", match=models.MatchValue(value=str(doc_id)))]
    if owner_id is not None:
        conditions.append(
            models.FieldCondition(key="owner_id", match=models.MatchValue(value=str(owner_id)))
        )
    return models.Filter(must=conditions)


async def delete_document_points(
    client: AsyncQdrantClient,
    doc_id: uuid.UUID,
    owner_id: uuid.UUID | None = None,
    name: str | None = None,
) -> None:
    """Delete every point of a document.

    Args:
        client: The Qdrant client.
        doc_id: The document.
        owner_id: Only delete points of this owner, if given.
        name: Collection name; defaults to ``settings.qdrant_collection``.
    """
    collection = name or settings.qdrant_collection
    if not await with_retries(lambda: client.collection_exists(collection), "collection_exists"):
        return  # nothing was ever stored
    await with_retries(
        lambda: client.delete(
            collection,
            points_selector=models.FilterSelector(filter=document_filter(doc_id, owner_id)),
            wait=True,
        ),
        "delete",
    )


async def count_document_points(
    client: AsyncQdrantClient, doc_id: uuid.UUID, name: str | None = None
) -> int:
    """Count a document's points (used to prove a delete worked).

    Args:
        client: The Qdrant client.
        doc_id: The document.
        name: Collection name; defaults to ``settings.qdrant_collection``.

    Returns:
        Exact number of points with this ``doc_id``.
    """
    collection = name or settings.qdrant_collection
    if not await with_retries(lambda: client.collection_exists(collection), "collection_exists"):
        return 0
    result = await with_retries(
        lambda: client.count(collection, count_filter=document_filter(doc_id), exact=True),
        "count",
    )
    return result.count
