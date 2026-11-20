"""Tests for app/retrieval.py: token regex, boosts, threshold, filters, RRF wiring.

The wiring tests store real chunks in in-memory Qdrant with the fake
embedders, so they exercise the actual Qdrant queries (prefetch + RRF,
filters) without loading any model.
"""

import uuid
from collections.abc import AsyncIterator

import pytest
from qdrant_client import AsyncQdrantClient

from app.config import settings
from app.ingestion.chunk import Chunk
from app.ingestion.embed import FakeEmbedder, FakeSparseEmbedder
from app.ingestion.store import DocumentMeta, ensure_collection, upsert_chunks
from app.retrieval import (
    ExactTokens,
    FakeReranker,
    RetrievalConfig,
    RetrievedChunk,
    Retrievers,
    apply_boosts,
    cosine,
    exact_tokens,
    has_exact_match,
    is_below_threshold,
    retrieve,
)

COLLECTION = "test_retrieval"
OWNER = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER_OWNER = uuid.UUID("00000000-0000-0000-0000-000000000002")
NO_BOOSTS = {"exact_boost": False, "authority_boost": False}


def make_chunk(**overrides: object) -> RetrievedChunk:
    """Build a RetrievedChunk with sensible defaults for pure-function tests."""
    values: dict[str, object] = {
        "chunk_id": "c1",
        "doc_id": "d1",
        "filename": "spec.pdf",
        "page_start": 1,
        "page_end": 1,
        "section_path": (),
        "chunk_type": "text",
        "authority": "official_guidance",
        "text": "",
        "similarity": 0.5,
        "rerank_score": 0.5,
        "exact_match": False,
        "score": 0.5,
        "rank": 1,
    }
    values.update(overrides)
    return RetrievedChunk(**values)  # type: ignore[arg-type]


# --- Exact tokens ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "codes", "sections"),
    [
        ("Our ERP got error 0052 back", {"0052"}, set()),
        ("which scenario is sn009?", {"SN009"}, set()),
        ("What does Rule 150XF say?", {"150XF"}, {"150XF"}),
        ("explain rule 150v", {"150V"}, {"150V"}),
        ("penalty under s.33", set(), {"33"}),
        ("penalty under section 33", set(), {"33"}),
        ("Is buyerNTNCNIC mandatory?", {"buyerNTNCNIC"}, set()),
        ("When do I fill invoiceRefNo?", {"invoiceRefNo"}, set()),
    ],
)
def test_exact_tokens_finds_identifiers(question: str, codes: set[str], sections: set[str]) -> None:
    tokens = exact_tokens(question)
    assert tokens.codes == codes
    assert tokens.sections == sections


@pytest.mark.parametrize(
    "question",
    [
        "What changed in 2025?",  # a year is not an error code
        "How long can the Commissioner extend the date?",  # plain words
        "We saw 00521 in the log",  # five digits is not a code
    ],
)
def test_exact_tokens_ignores_non_identifiers(question: str) -> None:
    assert not exact_tokens(question)


def test_exact_match_uses_whole_words_and_ignores_case() -> None:
    tokens = exact_tokens("error 0052 in scenario sn009")
    assert has_exact_match(make_chunk(text="| 0052 | Invalid buyer NTN |"), tokens)
    assert has_exact_match(make_chunk(text="Scenario SN009: cotton ginner"), tokens)
    assert not has_exact_match(make_chunk(text="code 00521 is different"), tokens)
    assert not has_exact_match(make_chunk(text="code SN0090"), tokens)


def test_section_numbers_match_headings_not_body_text() -> None:
    tokens = exact_tokens("What is the penalty under s.33?")
    heading = make_chunk(section_path=("Sales Tax Act", "33. Offences and penalties"))
    body = make_chunk(text="see clause 33 of the schedule", section_path=("9. Recovery",))
    assert has_exact_match(heading, tokens)
    assert not has_exact_match(body, tokens)


# --- Boosts and threshold ----------------------------------------------------------


def test_exact_boost_lifts_a_matching_chunk_past_a_near_tie() -> None:
    plain = make_chunk(chunk_id="plain", text="general error handling", rerank_score=0.60)
    exact = make_chunk(chunk_id="exact", text="| 0052 | Invalid NTN |", rerank_score=0.55)
    ranked = apply_boosts([plain, exact], exact_tokens("error 0052"), RetrievalConfig())
    assert [chunk.chunk_id for chunk in ranked] == ["exact", "plain"]
    assert ranked[0].exact_match is True
    assert ranked[0].score == pytest.approx(
        0.55 + settings.exact_token_boost + settings.authority_boosts["official_guidance"]
    )


def test_exact_boost_does_not_beat_a_clearly_better_chunk() -> None:
    better = make_chunk(chunk_id="better", rerank_score=0.95)
    exact = make_chunk(chunk_id="exact", text="0052", rerank_score=0.30)
    ranked = apply_boosts([better, exact], exact_tokens("error 0052"), RetrievalConfig())
    assert ranked[0].chunk_id == "better"


def test_authority_boost_puts_law_first_on_a_tie() -> None:
    guidance = make_chunk(chunk_id="guide", authority="official_guidance", rerank_score=0.5)
    law = make_chunk(chunk_id="law", authority="law", rerank_score=0.5)
    ranked = apply_boosts([guidance, law], ExactTokens(), RetrievalConfig())
    assert [chunk.chunk_id for chunk in ranked] == ["law", "guide"]


def test_boosts_switched_off_keep_rerank_order() -> None:
    first = make_chunk(chunk_id="a", rerank_score=0.6, authority="third_party")
    second = make_chunk(chunk_id="b", text="0052", rerank_score=0.5, authority="law")
    config = RetrievalConfig(**NO_BOOSTS)
    ranked = apply_boosts([first, second], exact_tokens("error 0052"), config)
    assert [chunk.chunk_id for chunk in ranked] == ["a", "b"]
    assert ranked[1].exact_match is False


def test_threshold_uses_the_raw_rerank_score_not_the_boosted_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "refusal_threshold", 0.30)
    # Boosted to 0.40 (above the 0.30 threshold), but the raw score is 0.20.
    weak = make_chunk(rerank_score=0.20, score=0.40)
    assert is_below_threshold([weak], best_rerank_score=0.20, config=RetrievalConfig())
    assert not is_below_threshold([weak], best_rerank_score=0.80, config=RetrievalConfig())


def test_threshold_without_rerank_only_refuses_when_nothing_found() -> None:
    config = RetrievalConfig(rerank=False, **NO_BOOSTS)
    assert not is_below_threshold([make_chunk(rerank_score=None)], None, config)
    assert is_below_threshold([], None, config)


def test_config_rejects_boosts_without_rerank() -> None:
    with pytest.raises(ValueError, match="rerank"):
        RetrievalConfig(rerank=False)
    with pytest.raises(ValueError):
        RetrievalConfig(top_k=0)


def test_cosine() -> None:
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


# --- Wiring with in-memory Qdrant --------------------------------------------------


@pytest.fixture
async def store() -> AsyncIterator[Retrievers]:
    """Yield retrievers over an in-memory collection with a small test corpus."""
    qdrant = AsyncQdrantClient(location=":memory:")
    retrievers = Retrievers(
        dense=FakeEmbedder(), sparse=FakeSparseEmbedder(), reranker=FakeReranker(), qdrant=qdrant
    )
    await ensure_collection(qdrant, COLLECTION)
    spec = [
        "Sales error codes table lists every validation error returned by the API",
        "| 0052 | Provide proper HS code with invoice |",
        "| 0401 | Unauthorised token for this seller |",
        "Error handling: retry the request when the API returns an error",
        "The buyerNTNCNIC field is optional for unregistered buyers",
        "Scenario SN009 covers a cotton spinner buying from a cotton ginner",
    ]
    rules = ["Rule 150V: the Commissioner may extend the date for integration by sixty days"]
    await _add(retrievers, "spec.pdf", spec, OWNER, authority="official_guidance")
    await _add(retrievers, "rules.pdf", rules, OWNER, authority="law")
    await _add(retrievers, "old.pdf", ["| 0052 | old superseded text |"], OWNER, superseded=True)
    await _add(retrievers, "theirs.pdf", ["| 0052 | another user's chunk |"], OTHER_OWNER)
    yield retrievers
    await qdrant.close()


async def _add(
    retrievers: Retrievers,
    filename: str,
    texts: list[str],
    owner: uuid.UUID,
    authority: str = "official_guidance",
    superseded: bool = False,
) -> None:
    """Embed and store one document's chunks (one page per chunk)."""
    chunks = [
        Chunk(
            text=text,
            embed_text=text,
            chunk_index=index,
            page_start=index + 1,
            page_end=index + 1,
            section_path=(),
            chunk_type="text",
        )
        for index, text in enumerate(texts)
    ]
    meta = DocumentMeta(
        doc_id=uuid.uuid4(),
        owner_id=owner,
        filename=filename,
        authority=authority,
        superseded=superseded,
    )
    dense = await retrievers.dense.embed_documents(texts)
    sparse = await retrievers.sparse.embed_documents(texts)
    await upsert_chunks(retrievers.qdrant, chunks, dense, sparse, meta, COLLECTION)


async def test_hybrid_rerank_puts_the_exact_code_row_first(store: Retrievers) -> None:
    result = await retrieve("What does error 0052 mean?", OWNER, store, collection=COLLECTION)
    top = result.chunks[0]
    assert "0052" in top.text
    assert top.exact_match is True
    assert top.rerank_score is not None
    assert result.best_rerank_score is not None
    assert [chunk.rank for chunk in result.chunks] == list(range(1, len(result.chunks) + 1))
    assert set(result.timings_ms) == {"embed", "search", "rerank"}


async def test_other_users_and_superseded_chunks_are_never_returned(store: Retrievers) -> None:
    config = RetrievalConfig(candidates=50, top_k=50)
    result = await retrieve("error 0052", OWNER, store, config, collection=COLLECTION)
    filenames = {chunk.filename for chunk in result.chunks}
    assert filenames <= {"spec.pdf", "rules.pdf"}
    assert "theirs.pdf" not in filenames
    assert "old.pdf" not in filenames


@pytest.mark.parametrize("mode", ["dense", "bm25", "hybrid"])
async def test_every_mode_returns_scored_chunks(store: Retrievers, mode: str) -> None:
    config = RetrievalConfig(mode=mode, rerank=False, **NO_BOOSTS)  # type: ignore[arg-type]
    result = await retrieve("cotton spinner scenario", OWNER, store, config, COLLECTION)
    assert result.chunks
    assert "SN009" in result.chunks[0].text
    assert all(chunk.rerank_score is None for chunk in result.chunks)
    assert all(-1.0 <= chunk.similarity <= 1.0 for chunk in result.chunks)
    assert result.best_rerank_score is None


async def test_similarity_is_the_cosine_with_the_stored_vector(store: Retrievers) -> None:
    question = "Commissioner extend integration date"
    result = await retrieve(question, OWNER, store, collection=COLLECTION)
    rule = next(chunk for chunk in result.chunks if chunk.filename == "rules.pdf")
    query_vector = await store.dense.embed_query(question)
    stored = (await store.dense.embed_documents([rule.text]))[0]
    assert rule.similarity == pytest.approx(cosine(query_vector, stored))


async def test_reranker_reorders_and_top_k_is_respected(store: Retrievers) -> None:
    config = RetrievalConfig(top_k=2, **NO_BOOSTS)
    result = await retrieve("cotton spinner ginner", OWNER, store, config, COLLECTION)
    assert len(result.chunks) == 2
    assert isinstance(store.reranker, FakeReranker) and store.reranker.calls == 1
    scores = [chunk.rerank_score or 0.0 for chunk in result.chunks]
    assert scores == sorted(scores, reverse=True)


async def test_only_rerank_candidates_reach_the_reranker(store: Retrievers) -> None:
    config = RetrievalConfig(rerank_candidates=2, top_k=6, **NO_BOOSTS)
    result = await retrieve("error code table", OWNER, store, config, COLLECTION)
    assert len(result.chunks) == 2
    assert all(chunk.rerank_score is not None for chunk in result.chunks)


async def test_weak_match_is_flagged_below_threshold(store: Retrievers) -> None:
    result = await retrieve("income tax salary slabs", OWNER, store, collection=COLLECTION)
    assert result.best_rerank_score is not None
    assert result.best_rerank_score < settings.refusal_threshold
    assert result.below_threshold is True


async def test_missing_collection_gives_an_empty_result(store: Retrievers) -> None:
    result = await retrieve("anything", OWNER, store, collection="does_not_exist")
    assert result.chunks == []
    assert result.below_threshold is True


async def test_user_without_documents_gets_an_empty_result(store: Retrievers) -> None:
    stranger = uuid.uuid4()
    result = await retrieve("error 0052", stranger, store, collection=COLLECTION)
    assert result.chunks == []
    assert result.below_threshold is True
