"""Unit tests for app.ingestion.store with in-memory Qdrant and fake embedders."""

import uuid

import httpx
import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse

from app.config import settings
from app.exceptions import UpstreamUnavailableError
from app.ingestion.chunk import Chunk
from app.ingestion.embed import FakeEmbedder, FakeSparseEmbedder
from app.ingestion.store import (
    DENSE_VECTOR,
    SPARSE_VECTOR,
    DocumentMeta,
    count_document_points,
    delete_document_points,
    ensure_collection,
    point_id,
    upsert_chunks,
    with_retries,
)

COLLECTION = "test_chunks"


def make_chunk(index: int, text: str = "150V. The Commissioner may allow extension.") -> Chunk:
    """Build a chunk as chunk.py would."""
    return Chunk(
        text=text,
        embed_text=f"Rules > CHAPTER XIV > Rule 150V\n\n{text}",
        chunk_index=index,
        page_start=108,
        page_end=109,
        section_path=("CHAPTER XIV", "Rule 150V"),
        chunk_type="legal_rule",
        ocr=False,
        amended_by=("S.R.O. 69(I)/2025",),
        suspicious=False,
    )


async def store(client: AsyncQdrantClient, meta: DocumentMeta, chunks: list[Chunk]) -> int:
    """Embed with fakes and upsert."""
    texts = [chunk.embed_text for chunk in chunks]
    dense = await FakeEmbedder().embed_documents(texts)
    sparse = await FakeSparseEmbedder().embed_documents(texts)
    return await upsert_chunks(client, chunks, dense, sparse, meta, name=COLLECTION)


@pytest.fixture
def meta() -> DocumentMeta:
    """Facts about one uploaded document."""
    return DocumentMeta(
        doc_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        filename="rules.pdf",
        authority="law",
        version_date="2026-07-31",
    )


async def test_collection_has_named_dense_and_bm25_vectors(qdrant: AsyncQdrantClient) -> None:
    await ensure_collection(qdrant, COLLECTION)
    await ensure_collection(qdrant, COLLECTION)  # second call is a no-op
    params = (await qdrant.get_collection(COLLECTION)).config.params
    assert params.vectors[DENSE_VECTOR].size == settings.embedding_dim
    assert params.vectors[DENSE_VECTOR].distance.value == "Cosine"
    assert params.sparse_vectors[SPARSE_VECTOR].modifier.value == "idf"


async def test_upsert_stores_full_payload(qdrant: AsyncQdrantClient, meta: DocumentMeta) -> None:
    await ensure_collection(qdrant, COLLECTION)
    assert await store(qdrant, meta, [make_chunk(0), make_chunk(1)]) == 2
    [point] = await qdrant.retrieve(COLLECTION, ids=[point_id(meta.doc_id, 0)])
    payload = point.payload
    assert set(payload) == {
        "chunk_id", "chunk_index", "owner_id", "doc_id", "filename", "page_start", "page_end",
        "section_path", "chunk_type", "authority", "version_date", "superseded", "amended_by",
        "ocr", "suspicious", "content_hash", "text",
    }  # fmt: skip
    assert payload["doc_id"] == str(meta.doc_id) and payload["owner_id"] == str(meta.owner_id)
    assert (payload["page_start"], payload["page_end"]) == (108, 109)
    assert payload["section_path"] == ["CHAPTER XIV", "Rule 150V"]
    assert payload["amended_by"] == ["S.R.O. 69(I)/2025"]
    assert payload["authority"] == "law" and payload["superseded"] is False
    assert payload["text"].startswith("150V.")  # the text without the context header


async def test_reingesting_overwrites_instead_of_duplicating(
    qdrant: AsyncQdrantClient, meta: DocumentMeta
) -> None:
    await ensure_collection(qdrant, COLLECTION)
    await store(qdrant, meta, [make_chunk(0), make_chunk(1)])
    await store(qdrant, meta, [make_chunk(0), make_chunk(1)])
    assert await count_document_points(qdrant, meta.doc_id, name=COLLECTION) == 2


async def test_delete_leaves_zero_points_and_spares_other_documents(
    qdrant: AsyncQdrantClient, meta: DocumentMeta
) -> None:
    other = DocumentMeta(doc_id=uuid.uuid4(), owner_id=meta.owner_id, filename="spec.pdf")
    await ensure_collection(qdrant, COLLECTION)
    await store(qdrant, meta, [make_chunk(0), make_chunk(1), make_chunk(2)])
    await store(qdrant, other, [make_chunk(0)])

    await delete_document_points(qdrant, meta.doc_id, meta.owner_id, name=COLLECTION)

    assert await count_document_points(qdrant, meta.doc_id, name=COLLECTION) == 0
    assert await count_document_points(qdrant, other.doc_id, name=COLLECTION) == 1


async def test_delete_with_wrong_owner_removes_nothing(
    qdrant: AsyncQdrantClient, meta: DocumentMeta
) -> None:
    await ensure_collection(qdrant, COLLECTION)
    await store(qdrant, meta, [make_chunk(0)])
    await delete_document_points(qdrant, meta.doc_id, uuid.uuid4(), name=COLLECTION)
    assert await count_document_points(qdrant, meta.doc_id, name=COLLECTION) == 1


async def test_delete_and_count_before_any_upload(qdrant: AsyncQdrantClient) -> None:
    await delete_document_points(qdrant, uuid.uuid4(), name="never_created")
    assert await count_document_points(qdrant, uuid.uuid4(), name="never_created") == 0


async def test_mismatched_vectors_are_rejected(
    qdrant: AsyncQdrantClient, meta: DocumentMeta
) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        await upsert_chunks(qdrant, [make_chunk(0)], [], [], meta, name=COLLECTION)


def test_point_ids_are_stable() -> None:
    doc_id = uuid.uuid4()
    assert point_id(doc_id, 3) == point_id(doc_id, 3) != point_id(doc_id, 4)


# --- Retries -----------------------------------------------------------------


@pytest.fixture
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """No real waiting between attempts."""
    monkeypatch.setattr("app.ingestion.store.wait_exponential", lambda **_: lambda _state: 0)


async def test_transient_error_is_retried_then_succeeds(fast_retries: None) -> None:
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("connection refused")
        return "ok"

    assert await with_retries(flaky, "test") == "ok"
    assert attempts == 3


async def test_persistent_outage_becomes_503(fast_retries: None) -> None:
    async def down() -> None:
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(UpstreamUnavailableError):
        await with_retries(down, "test")


async def test_client_error_is_not_retried(fast_retries: None) -> None:
    attempts = 0

    async def bad_request() -> None:
        nonlocal attempts
        attempts += 1
        raise UnexpectedResponse(400, "Bad Request", b"{}", httpx.Headers())

    with pytest.raises(UnexpectedResponse):
        await with_retries(bad_request, "test")
    assert attempts == 1
