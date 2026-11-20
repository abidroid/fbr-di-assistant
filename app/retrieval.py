"""Retrieval: find the chunks that answer a question.

Pipeline (each step can be switched off by a `RetrievalConfig`, so the
evaluation can measure what every step adds):

1. Embed the question twice at the same time: dense (meaning) and bm25 (exact words).
2. Ask Qdrant for the top ``candidates`` of each, filtered to the user's own,
   current (not superseded) chunks, and fuse both lists with RRF.
3. Rerank the fused candidates with a cross-encoder, which reads question and
   chunk together and is far more accurate than comparing two vectors.
4. Add small boosts (exact code match, law over guidance) and keep ``top_k``.
5. Flag the result as below the refusal threshold when even the best raw
   rerank score is low: the documents probably do not contain the answer.
"""

import os
import re
from typing import Any, Protocol

import anyio

from app.config import settings

# Files in the reranker repository that PyTorch inference does not need.
MODEL_SKIP = ["onnx/*", "imgs/*", "*.jpg", "*.webp"]


class Reranker(Protocol):
    """Anything that scores how well passages answer a question."""

    async def score(self, query: str, passages: list[str]) -> list[float]:
        """Score each passage against the question (higher is better)."""
        ...


class BGEReranker:
    """Cross-encoder reranker bge-reranker-v2-m3 on CPU, loaded once in the lifespan.

    A cross-encoder sees question and passage in one input, so every word of
    the question can attend to every word of the passage. That is why it ranks
    better than vector similarity, and also why it is slow: one model pass per
    pair, which is why only the fused candidates are reranked.
    """

    def __init__(self) -> None:
        """Load the model (about 2.3 GB once downloaded)."""
        # Imported here: the import pulls in torch, which the tests never need.
        import torch
        from FlagEmbedding import FlagReranker
        from huggingface_hub import snapshot_download

        torch.set_num_threads(settings.embed_threads or os.cpu_count() or 1)
        local_path = snapshot_download(settings.reranker_model, ignore_patterns=MODEL_SKIP)
        # fp16 speeds up GPUs but is slow or unsupported on CPU.
        self._model = FlagReranker(local_path, use_fp16=False, devices="cpu")

    async def score(self, query: str, passages: list[str]) -> list[float]:
        """Score passages in a worker thread, so the event loop stays free.

        Args:
            query: The question.
            passages: Candidate chunk texts.

        Returns:
            One score between 0 and 1 per passage, in the same order.
        """
        if not passages:
            return []
        return await anyio.to_thread.run_sync(self._score, query, passages)

    def _score(self, query: str, passages: list[str]) -> list[float]:
        """Run the model (blocking).

        Args:
            query: The question.
            passages: Candidate chunk texts.

        Returns:
            Scores squashed to 0-1 by a sigmoid (``normalize=True``), so one
            refusal threshold means the same thing for every question.
        """
        scores: Any = self._model.compute_score(
            [[query, passage] for passage in passages],
            batch_size=settings.rerank_batch_size,
            max_length=settings.rerank_max_tokens,
            normalize=True,
        )
        # FlagEmbedding returns a bare float when given a single pair.
        if isinstance(scores, float | int):
            scores = [scores]
        return [float(value) for value in scores]


class FakeReranker:
    """Deterministic reranker for tests: share of question words in the passage.

    Attributes:
        calls: How many times `score` was called.
    """

    def __init__(self) -> None:
        """Create the fake."""
        self.calls = 0

    async def score(self, query: str, passages: list[str]) -> list[float]:
        """Return, per passage, the fraction of the question's words it contains."""
        self.calls += 1
        query_words = set(_words(query))
        if not query_words:
            return [0.0 for _ in passages]
        return [len(query_words & set(_words(passage))) / len(query_words) for passage in passages]


def create_reranker() -> Reranker:
    """Build the reranker (called once in the app's lifespan).

    Returns:
        The real bge-reranker-v2-m3 reranker.
    """
    return BGEReranker()


def _words(text: str) -> list[str]:
    """Lower-case words and numbers of a text."""
    return re.findall(r"\w+", text.lower())
