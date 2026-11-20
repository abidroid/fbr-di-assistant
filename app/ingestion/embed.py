"""Embeddings: turn text into the two kinds of vectors stored in Qdrant.

- Dense (BGE-M3, 1024 numbers, normalised): captures meaning, so "deadline
  extension" lands near "Commissioner may allow extension in time".
- Sparse (BM25 via fastembed ``Qdrant/bm25``): which exact words occur, so
  codes like 0052 or SN009 match exactly. Qdrant adds the IDF weight (how
  rare a word is in the collection) at search time.

Both hide behind small protocols, so tests use `FakeEmbedder` and
`FakeSparseEmbedder` and never load a model. The models are slow CPU work,
so every call runs in a worker thread and the event loop stays free.
"""

import hashlib
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol

import anyio

from app.config import settings

# Files in the BGE-M3 repository that PyTorch inference does not need.
MODEL_SKIP = ["onnx/*", "imgs/*", "*.jpg", "*.webp"]


@dataclass(frozen=True)
class SparseVector:
    """A sparse vector: only the non-zero positions and their values.

    Attributes:
        indices: Token ids that occur in the text.
        values: Weight of each token (BM25 term frequency part).
    """

    indices: list[int]
    values: list[float]


class DenseEmbedder(Protocol):
    """Anything that turns text into dense vectors of a fixed size."""

    dim: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed chunk texts (batched)."""
        ...

    async def embed_query(self, text: str) -> list[float]:
        """Embed one search question."""
        ...


class SparseEmbedder(Protocol):
    """Anything that turns text into BM25-style sparse vectors."""

    async def embed_documents(self, texts: list[str]) -> list[SparseVector]:
        """Embed chunk texts (batched)."""
        ...

    async def embed_query(self, text: str) -> SparseVector:
        """Embed one search question."""
        ...


class BGEM3Embedder:
    """Dense embeddings from BGE-M3 on CPU, loaded once in the app's lifespan.

    Attributes:
        dim: Vector size (1024).
    """

    def __init__(self) -> None:
        """Load the model (about 20 s and 2 GB of RAM once downloaded)."""
        # Imported here: the import pulls in torch, which the tests never need.
        import torch
        from FlagEmbedding import BGEM3FlagModel
        from huggingface_hub import snapshot_download

        self.dim = settings.embedding_dim
        torch.set_num_threads(settings.embed_threads or os.cpu_count() or 1)
        # Given only a name, FlagEmbedding downloads the whole repository,
        # including a 2.3 GB ONNX copy we never use. Downloading the PyTorch
        # files ourselves and passing the local folder halves the download.
        local_path = snapshot_download(settings.embedding_model, ignore_patterns=MODEL_SKIP)
        # fp16 speeds up GPUs but is slow or unsupported on CPU.
        self._model = BGEM3FlagModel(
            local_path, use_fp16=False, devices="cpu", normalize_embeddings=True
        )

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed chunk texts in batches, in a worker thread.

        Args:
            texts: Texts to embed (chunk ``embed_text``).

        Returns:
            One normalised vector per text, in the same order.
        """
        return await anyio.to_thread.run_sync(self._encode, texts)

    async def embed_query(self, text: str) -> list[float]:
        """Embed one question.

        Args:
            text: The question.

        Returns:
            Its normalised vector.
        """
        return (await anyio.to_thread.run_sync(self._encode, [text]))[0]

    def _encode(self, texts: list[str]) -> list[list[float]]:
        """Run the model (blocking).

        Args:
            texts: Texts to embed.

        Returns:
            Normalised vectors as plain lists (what Qdrant accepts).
        """
        output = self._model.encode(
            texts,
            batch_size=settings.embed_batch_size,
            max_length=settings.embed_max_tokens,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
        return [_normalise(list(map(float, vector))) for vector in output["dense_vecs"]]


class Bm25Embedder:
    """Sparse BM25 vectors from fastembed's ``Qdrant/bm25`` (tiny, no GPU)."""

    def __init__(self) -> None:
        """Load the tokenizer and stemmer (a few MB)."""
        from fastembed import SparseTextEmbedding

        self._model = SparseTextEmbedding(settings.sparse_model)

    async def embed_documents(self, texts: list[str]) -> list[SparseVector]:
        """Embed chunk texts in a worker thread.

        Args:
            texts: Texts to embed.

        Returns:
            One sparse vector per text.
        """
        return await anyio.to_thread.run_sync(self._embed, texts, False)

    async def embed_query(self, text: str) -> SparseVector:
        """Embed one question (every query word weighted 1; Qdrant adds IDF).

        Args:
            text: The question.

        Returns:
            Its sparse vector.
        """
        return (await anyio.to_thread.run_sync(self._embed, [text], True))[0]

    def _embed(self, texts: list[str], query: bool) -> list[SparseVector]:
        """Run fastembed (blocking).

        Args:
            texts: Texts to embed.
            query: Use query weighting instead of document weighting.

        Returns:
            Sparse vectors.
        """
        if query:
            results: Any = self._model.query_embed(texts)
        else:
            results = self._model.embed(texts, batch_size=settings.embed_batch_size)
        return [SparseVector(r.indices.tolist(), r.values.tolist()) for r in results]


class FakeEmbedder:
    """Deterministic dense vectors for tests: same text, same vector.

    Texts that share words get similar vectors, so simple search tests work.

    Attributes:
        dim: Vector size (same as the real model, so the collection matches).
        calls: How many batches were embedded.
    """

    def __init__(self, dim: int | None = None) -> None:
        """Create the fake.

        Args:
            dim: Vector size; defaults to ``settings.embedding_dim``.
        """
        self.dim = dim or settings.embedding_dim
        self.calls = 0

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per text."""
        self.calls += 1
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        """Return the vector for one question."""
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        """Add a fixed position per word, then normalise."""
        vector = [0.0] * self.dim
        for word in _words(text):
            vector[_hash(word) % self.dim] += 1.0
        return _normalise(vector) if any(vector) else [1.0] + [0.0] * (self.dim - 1)


class FakeSparseEmbedder:
    """Deterministic sparse vectors for tests: word hash -> count."""

    async def embed_documents(self, texts: list[str]) -> list[SparseVector]:
        """Return one sparse vector per text."""
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> SparseVector:
        """Return the sparse vector for one question."""
        return self._vector(text)

    def _vector(self, text: str) -> SparseVector:
        """Count each word under a stable id."""
        counts: dict[int, float] = {}
        for word in _words(text):
            index = _hash(word) % 1_000_000
            counts[index] = counts.get(index, 0.0) + 1.0
        return SparseVector(list(counts), list(counts.values()))


def create_embedders() -> tuple[DenseEmbedder, SparseEmbedder]:
    """Build the configured embedders (called once in the app's lifespan).

    Returns:
        The dense and sparse embedder.

    Raises:
        NotImplementedError: For ``EMBEDDER=api``; the interface allows a
            hosted embedding API later without touching the pipeline.
    """
    if settings.embedder == "api":
        raise NotImplementedError("EMBEDDER=api is not implemented yet; use EMBEDDER=local.")
    return BGEM3Embedder(), Bm25Embedder()


def _normalise(vector: list[float]) -> list[float]:
    """Scale a vector to length 1, so dot product equals cosine similarity."""
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def _words(text: str) -> list[str]:
    """Lower-case words and numbers of a text."""
    return re.findall(r"\w+", text.lower())


def _hash(word: str) -> int:
    """A stable hash (Python's own hash() changes between runs)."""
    return int.from_bytes(hashlib.blake2b(word.encode(), digest_size=8).digest(), "big")
