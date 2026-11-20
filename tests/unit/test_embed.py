"""Unit tests for app.ingestion.embed (stub models; nothing is downloaded)."""

import math
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from app.config import settings
from app.ingestion.embed import (
    BGEM3Embedder,
    Bm25Embedder,
    FakeEmbedder,
    FakeSparseEmbedder,
    create_embedders,
)


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors."""
    return sum(x * y for x, y in zip(a, b, strict=True)) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    )


async def test_fake_embedder_is_deterministic_and_normalised() -> None:
    fake = FakeEmbedder()
    first, again = await fake.embed_documents(["Rule 150V extension", "Rule 150V extension"])
    assert first == again
    assert len(first) == settings.embedding_dim
    assert math.isclose(sum(v * v for v in first), 1.0)


async def test_fake_embedder_puts_shared_words_closer() -> None:
    fake = FakeEmbedder()
    query = await fake.embed_query("extension of integration date")
    near, far = await fake.embed_documents(
        ["extension in due date of integration", "HS code for fertiliser"]
    )
    assert cosine(query, near) > cosine(query, far)


async def test_fake_sparse_embedder_counts_words() -> None:
    [vector] = await FakeSparseEmbedder().embed_documents(["0052 0052 hs code"])
    assert sorted(vector.values) == [1.0, 1.0, 2.0]
    assert vector == await FakeSparseEmbedder().embed_query("0052 0052 hs code")


class StubBGEM3:
    """Records encode() arguments and returns fixed, unnormalised vectors."""

    def __init__(self) -> None:
        """Start with no recorded call."""
        self.kwargs: dict[str, Any] = {}

    def encode(self, texts: list[str], **kwargs: Any) -> dict[str, Any]:
        """Pretend to run BGE-M3."""
        self.kwargs = kwargs
        return {"dense_vecs": np.array([[3.0, 4.0] for _ in texts])}


async def test_bgem3_passes_max_length_and_normalises() -> None:
    embedder = object.__new__(BGEM3Embedder)  # skip __init__: no model download
    stub = StubBGEM3()
    embedder._model = stub
    embedder.dim = 2
    [vector] = await embedder.embed_documents(["a long JSON sample"])
    # Without max_length the library cuts every text at 512 tokens.
    assert stub.kwargs["max_length"] == settings.embed_max_tokens
    assert stub.kwargs["batch_size"] == settings.embed_batch_size
    assert stub.kwargs["return_sparse"] is False
    assert vector == pytest.approx([0.6, 0.8])
    assert await embedder.embed_query("q") == pytest.approx([0.6, 0.8])


class StubBm25:
    """Returns one fixed sparse embedding per text, separately for queries."""

    def _result(self, weight: float) -> SimpleNamespace:
        return SimpleNamespace(indices=np.array([7, 42]), values=np.array([weight, 0.5]))

    def embed(self, texts: list[str], batch_size: int) -> list[SimpleNamespace]:
        """Document embedding."""
        return [self._result(1.5) for _ in texts]

    def query_embed(self, texts: list[str]) -> list[SimpleNamespace]:
        """Query embedding."""
        return [self._result(1.0) for _ in texts]


async def test_bm25_uses_document_and_query_modes() -> None:
    embedder = object.__new__(Bm25Embedder)
    embedder._model = StubBm25()
    [document] = await embedder.embed_documents(["text"])
    query = await embedder.embed_query("text")
    assert document.indices == [7, 42] and document.values == [1.5, 0.5]
    assert query.values == [1.0, 0.5]


def test_api_embedder_is_not_implemented(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "embedder", "api")
    with pytest.raises(NotImplementedError):
        create_embedders()
