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

import asyncio
import math
import os
import re
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

import anyio
from qdrant_client import AsyncQdrantClient, models

from app.config import settings
from app.ingestion.embed import DenseEmbedder, SparseEmbedder, SparseVector
from app.ingestion.store import DENSE_VECTOR, SPARSE_VECTOR, with_retries

# Files in the reranker repository that PyTorch inference does not need.
MODEL_SKIP = ["onnx/*", "imgs/*", "*.jpg", "*.webp"]

Mode = Literal["dense", "bm25", "hybrid"]


# --- Reranker ----------------------------------------------------------------------


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


# --- Configuration and results -----------------------------------------------------


@dataclass(frozen=True)
class RetrievalConfig:
    """Which retrieval steps run. The defaults are the final system.

    Each evaluation config builds one of these, so a single code path serves
    both the API and every experiment.

    Attributes:
        mode: ``dense`` (meaning only), ``bm25`` (words only) or ``hybrid``
            (both, fused with RRF).
        rerank: Rerank the candidates with the cross-encoder.
        exact_boost: Lift chunks containing an exact code from the question.
        authority_boost: Lift law above guidance on near-ties.
        candidates: How many chunks each search returns before reranking.
        top_k: How many chunks are kept at the end.
    """

    mode: Mode = "hybrid"
    rerank: bool = True
    exact_boost: bool = True
    authority_boost: bool = True
    candidates: int = field(default_factory=lambda: settings.candidates)
    top_k: int = field(default_factory=lambda: settings.top_k)

    def __post_init__(self) -> None:
        """Reject combinations that make no sense.

        Raises:
            ValueError: If boosts are on without reranking (boosts are sized
                for 0-1 rerank scores; RRF scores are about 0.03, so a 0.15
                boost would override the ranking entirely), or if a size is
                not positive.
        """
        if (self.exact_boost or self.authority_boost) and not self.rerank:
            raise ValueError("Boosts need rerank=true: they are added to rerank scores.")
        if self.candidates < 1 or self.top_k < 1:
            raise ValueError("candidates and top_k must be at least 1.")


@dataclass(frozen=True)
class RetrievedChunk:
    """One chunk returned by retrieval, with everything a citation needs.

    Attributes:
        chunk_id: Qdrant point id.
        doc_id: Document id.
        filename: Document file name.
        page_start: First page of the chunk.
        page_end: Last page of the chunk.
        section_path: Headings above the chunk, outermost first.
        chunk_type: e.g. ``legal_rule``, ``table_row``, ``faq``.
        authority: ``law``, ``official_guidance``, ``faq`` or ``third_party``.
        text: The chunk text (without the context header).
        similarity: Cosine similarity between question and chunk (-1 to 1);
            shown in the chunks panel. RRF scores are ranks, not similarities.
        rerank_score: Cross-encoder score (0-1), or None when reranking is off.
        exact_match: The chunk contains an exact code from the question.
        score: The value the final order is sorted by.
        rank: Position in the final list, 1 = best.
    """

    chunk_id: str
    doc_id: str
    filename: str
    page_start: int
    page_end: int
    section_path: tuple[str, ...]
    chunk_type: str
    authority: str
    text: str
    similarity: float
    rerank_score: float | None
    exact_match: bool
    score: float
    rank: int


@dataclass(frozen=True)
class RetrievalResult:
    """What retrieval found for one question.

    Attributes:
        chunks: The final ``top_k`` chunks, best first.
        best_rerank_score: Highest RAW rerank score among all candidates
            (before boosts), or None when reranking is off or nothing was found.
        below_threshold: True when the system should refuse: nothing was found,
            or the best raw rerank score is under ``REFUSAL_THRESHOLD``.
        timings_ms: Milliseconds spent in ``embed``, ``search`` and ``rerank``.
    """

    chunks: list[RetrievedChunk]
    best_rerank_score: float | None
    below_threshold: bool
    timings_ms: dict[str, float]


@dataclass(frozen=True)
class Retrievers:
    """The long-lived objects retrieval needs, created once in the lifespan.

    Attributes:
        dense: Dense embedder (BGE-M3, or a fake in tests).
        sparse: Sparse embedder (BM25, or a fake in tests).
        reranker: Cross-encoder (or a fake in tests).
        qdrant: Vector database client.
    """

    dense: DenseEmbedder
    sparse: SparseEmbedder
    reranker: Reranker
    qdrant: AsyncQdrantClient


# --- The pipeline ------------------------------------------------------------------


async def retrieve(
    question: str,
    owner_id: uuid.UUID,
    retrievers: Retrievers,
    config: RetrievalConfig | None = None,
    collection: str | None = None,
) -> RetrievalResult:
    """Find the chunks of this user's documents that best answer a question.

    Args:
        question: The (already rewritten, if a follow-up) question.
        owner_id: Only this user's chunks are searched.
        retrievers: Embedders, reranker and Qdrant client.
        config: Which steps run; defaults to the final system.
        collection: Qdrant collection; defaults to ``settings.qdrant_collection``.

    Returns:
        The top chunks, the best raw rerank score, the refusal flag and timings.

    Raises:
        UpstreamUnavailableError: If Qdrant stays unreachable after retries (503).
    """
    config = config or RetrievalConfig()
    collection = collection or settings.qdrant_collection
    timings: dict[str, float] = {}

    started = time.perf_counter()
    dense_vector, sparse_vector = await embed_query(question, retrievers, config.mode)
    timings["embed"] = _ms_since(started)

    started = time.perf_counter()
    points = await search_candidates(
        retrievers.qdrant, collection, dense_vector, sparse_vector, owner_id, config
    )
    chunks = [to_chunk(point, dense_vector, rank) for rank, point in enumerate(points, start=1)]
    timings["search"] = _ms_since(started)

    started = time.perf_counter()
    best_rerank_score: float | None = None
    if config.rerank and chunks:
        chunks = await rerank(question, chunks, retrievers.reranker)
        best_rerank_score = max(chunk.rerank_score or 0.0 for chunk in chunks)
        chunks = apply_boosts(chunks, exact_tokens(question), config)
    timings["rerank"] = _ms_since(started)

    top = [replace(chunk, rank=rank) for rank, chunk in enumerate(chunks[: config.top_k], 1)]
    return RetrievalResult(
        chunks=top,
        best_rerank_score=best_rerank_score,
        below_threshold=is_below_threshold(top, best_rerank_score, config),
        timings_ms=timings,
    )


async def embed_query(
    question: str, retrievers: Retrievers, mode: Mode
) -> tuple[list[float], SparseVector | None]:
    """Embed the question for the searches the mode needs.

    The dense vector is always made: besides dense search it gives the cosine
    similarity shown for every chunk. Dense and bm25 run concurrently because
    they are independent.

    Args:
        question: The question.
        retrievers: Holds the embedders.
        mode: Retrieval mode.

    Returns:
        The dense vector, and the sparse vector (None in dense-only mode).
    """
    if mode == "dense":
        return await retrievers.dense.embed_query(question), None
    dense, sparse = await asyncio.gather(
        retrievers.dense.embed_query(question), retrievers.sparse.embed_query(question)
    )
    return dense, sparse


def search_filter(owner_id: uuid.UUID) -> models.Filter:
    """Build the filter every search uses: own chunks, current versions only.

    It runs inside Qdrant (on the payload indexes), so another user's chunks
    and superseded versions are never even candidates.

    Args:
        owner_id: The user asking.

    Returns:
        A Qdrant filter.
    """
    return models.Filter(
        must=[
            models.FieldCondition(key="owner_id", match=models.MatchValue(value=str(owner_id))),
            models.FieldCondition(key="superseded", match=models.MatchValue(value=False)),
        ]
    )


async def search_candidates(
    qdrant: AsyncQdrantClient,
    collection: str,
    dense_vector: list[float],
    sparse_vector: SparseVector | None,
    owner_id: uuid.UUID,
    config: RetrievalConfig,
) -> list[models.ScoredPoint]:
    """Run the search for the configured mode in one Qdrant request.

    Hybrid sends two prefetches (dense and bm25, ``candidates`` each, both
    filtered) and Qdrant fuses them with RRF: every chunk gets
    ``1 / (k + rank)`` from each list it appears in, summed. Only ranks are
    used, because cosine and BM25 scores are on different scales.

    Args:
        qdrant: The Qdrant client.
        collection: Collection to search.
        dense_vector: The question's dense vector.
        sparse_vector: The question's sparse vector (needed for bm25/hybrid).
        owner_id: The user asking.
        config: Mode and candidate count.

    Returns:
        Up to ``candidates`` points, best first, with payload and dense vector;
        empty if the collection does not exist yet (nothing uploaded).
    """
    if not await with_retries(lambda: qdrant.collection_exists(collection), "collection_exists"):
        return []
    query_filter = search_filter(owner_id)
    request: dict[str, Any] = {
        "limit": config.candidates,
        "query_filter": query_filter,
        "with_payload": True,
        # The stored dense vector gives each chunk's cosine similarity.
        "with_vectors": [DENSE_VECTOR],
    }
    sparse_query = (
        models.SparseVector(indices=sparse_vector.indices, values=sparse_vector.values)
        if sparse_vector is not None
        else None
    )
    if config.mode == "hybrid" and sparse_query is not None:
        request["prefetch"] = [
            models.Prefetch(query=query, using=name, limit=config.candidates, filter=query_filter)
            for query, name in ((dense_vector, DENSE_VECTOR), (sparse_query, SPARSE_VECTOR))
        ]
        request["query"] = models.RrfQuery(rrf=models.Rrf(k=settings.rrf_k))
    elif config.mode == "bm25" and sparse_query is not None:
        request["query"] = sparse_query
        request["using"] = SPARSE_VECTOR
    else:
        request["query"] = dense_vector
        request["using"] = DENSE_VECTOR
    response = await with_retries(
        lambda: qdrant.query_points(collection, **request), "query_points"
    )
    return response.points


def to_chunk(point: models.ScoredPoint, query_vector: list[float], rank: int) -> RetrievedChunk:
    """Turn a Qdrant point into a `RetrievedChunk`.

    Args:
        point: A search result with payload and dense vector.
        query_vector: The question's dense vector (for cosine similarity).
        rank: Its position in the search result.

    Returns:
        The chunk, scored by the search (RRF, cosine or BM25) until reranked.
    """
    payload = point.payload or {}
    vectors = point.vector if isinstance(point.vector, dict) else {}
    stored = vectors.get(DENSE_VECTOR)
    similarity = cosine(query_vector, stored) if isinstance(stored, list) else 0.0
    page_start = int(payload.get("page_start") or 0)
    return RetrievedChunk(
        chunk_id=str(point.id),
        doc_id=payload.get("doc_id", ""),
        filename=payload.get("filename", ""),
        page_start=page_start,
        page_end=int(payload.get("page_end") or page_start),
        section_path=tuple(payload.get("section_path") or ()),
        chunk_type=payload.get("chunk_type", ""),
        authority=payload.get("authority", "third_party"),
        text=payload.get("text", ""),
        similarity=similarity,
        rerank_score=None,
        exact_match=False,
        score=point.score,
        rank=rank,
    )


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        A value from -1 to 1 (0 if either vector is all zeros).
    """
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / norm if norm else 0.0


async def rerank(
    question: str, chunks: list[RetrievedChunk], reranker: Reranker
) -> list[RetrievedChunk]:
    """Score every candidate with the cross-encoder and sort by that score.

    Args:
        question: The question.
        chunks: Candidates from the search.
        reranker: The cross-encoder.

    Returns:
        The same chunks with ``rerank_score`` and ``score`` set, best first.
    """
    scores = await reranker.score(question, [rerank_text(chunk) for chunk in chunks])
    scored = [
        replace(chunk, rerank_score=value, score=value)
        for chunk, value in zip(chunks, scores, strict=True)
    ]
    return sorted(scored, key=lambda chunk: chunk.score, reverse=True)


def rerank_text(chunk: RetrievedChunk) -> str:
    """Return what the reranker reads: section path, then the chunk text.

    The path tells the reranker, for example, that a bare table row belongs
    to "Sales Error Codes".

    Args:
        chunk: A candidate.

    Returns:
        The text to score.
    """
    if not chunk.section_path:
        return chunk.text
    return " > ".join(chunk.section_path) + "\n" + chunk.text


def apply_boosts(
    chunks: list[RetrievedChunk], tokens: "ExactTokens", config: RetrievalConfig
) -> list[RetrievedChunk]:
    """Add the exact-code and authority boosts to rerank scores and re-sort.

    Args:
        chunks: Reranked chunks.
        tokens: Exact identifiers found in the question.
        config: Which boosts are on.

    Returns:
        The chunks with ``exact_match`` and the boosted ``score``, best first.
        Python's sort is stable, so equal scores keep the reranker's order.
    """
    boosted = []
    for chunk in chunks:
        score = chunk.rerank_score or 0.0
        matched = config.exact_boost and has_exact_match(chunk, tokens)
        if matched:
            score += settings.exact_token_boost
        if config.authority_boost:
            score += settings.authority_boosts.get(chunk.authority, 0.0)
        boosted.append(replace(chunk, exact_match=matched, score=score))
    return sorted(boosted, key=lambda chunk: chunk.score, reverse=True)


def is_below_threshold(
    chunks: list[RetrievedChunk], best_rerank_score: float | None, config: RetrievalConfig
) -> bool:
    """Decide whether the answer should be a refusal.

    The RAW rerank score is used, never the boosted one: a boost reorders
    near-ties but must not turn a weak match into a confident one.

    Args:
        chunks: The final chunks.
        best_rerank_score: Highest raw rerank score among the candidates.
        config: Whether reranking ran.

    Returns:
        True if nothing was found, or reranking ran and its best score is
        under ``REFUSAL_THRESHOLD``. Without reranking there is no calibrated
        score, so only "nothing found" counts.
    """
    if not chunks:
        return True
    if not config.rerank or best_rerank_score is None:
        return False
    return best_rerank_score < settings.refusal_threshold


# --- Exact-token matching ----------------------------------------------------------

# FBR error codes are four digits with a leading zero (0052, 0401). Requiring
# the zero keeps years such as 2025 out.
ERROR_CODE = re.compile(r"\b0\d{3}\b")
# Scenario ids in the API spec: SN001 ... SN028.
SCENARIO_ID = re.compile(r"\bSN\d{3}\b", re.IGNORECASE)
# Rule numbers with a letter suffix as printed in the Rules: 150V, 150XF.
RULE_NUMBER = re.compile(r"\b\d{2,3}[A-Z]{1,3}\b")
# An explicit reference written by the user: "rule 150v", "section 33", "s.33".
SECTION_REF = re.compile(r"\b(?:rule|section|s\.)\s*(\d{1,3}[A-Za-z]{0,3})\b", re.IGNORECASE)
# camelCase JSON field names from the API spec: invoiceRefNo, sellerNTNCNIC.
CAMEL_CASE = re.compile(r"\b[a-z]+(?:[A-Z][a-z0-9]*)+\b")


@dataclass(frozen=True)
class ExactTokens:
    """Exact identifiers found in a question.

    Attributes:
        codes: Looked for in a chunk's TEXT: error codes, scenario ids, rule
            numbers with letters, field names.
        sections: Section/rule numbers looked for in a chunk's SECTION PATH.
            A bare number like 33 appears all over the text, but the heading
            "33. Offences and penalties" is where section 33 actually is.
    """

    codes: frozenset[str] = frozenset()
    sections: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        """True if the question contained any exact identifier."""
        return bool(self.codes or self.sections)


def exact_tokens(question: str) -> ExactTokens:
    """Find the exact identifiers in a question.

    Args:
        question: The question.

    Returns:
        Codes to match in chunk text and section numbers to match in headings.
    """
    codes = set(ERROR_CODE.findall(question))
    codes |= {match.upper() for match in SCENARIO_ID.findall(question)}
    codes |= set(RULE_NUMBER.findall(question))
    codes |= set(CAMEL_CASE.findall(question))
    sections = {match.upper() for match in SECTION_REF.findall(question)}
    # "rule 150xf" typed in lower case is still a code worth finding in text.
    codes |= {number for number in sections if not number.isdigit()}
    return ExactTokens(codes=frozenset(codes), sections=frozenset(sections))


def has_exact_match(chunk: RetrievedChunk, tokens: ExactTokens) -> bool:
    """Say whether a chunk contains one of the question's exact identifiers.

    Matching is whole-word, so 0052 does not match 00521 and 150V does not
    match 150VA. Case is ignored (users type sn009).

    Args:
        chunk: A candidate chunk.
        tokens: Identifiers from the question.

    Returns:
        True on any match.
    """
    if not tokens:
        return False
    if any(_contains_word(chunk.text, code) for code in tokens.codes):
        return True
    headings = " > ".join(chunk.section_path)
    return any(_contains_word(headings, number) for number in tokens.sections)


def _contains_word(text: str, word: str) -> bool:
    """Case-insensitive whole-word search."""
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.IGNORECASE) is not None


def _words(text: str) -> list[str]:
    """Lower-case words and numbers of a text."""
    return re.findall(r"\w+", text.lower())


def _ms_since(started: float) -> float:
    """Milliseconds since a ``time.perf_counter()`` reading, rounded to 0.1."""
    return round((time.perf_counter() - started) * 1000, 1)
