"""Answer one question: the /ask use case.

Flow: (no searchable documents -> fixed message) -> rewrite a follow-up ->
retrieve -> refuse without an LLM call if retrieval is under the threshold ->
generate a cited answer -> output leak check -> `AskResponse`.

`answer_question` needs no database, so the evaluation runs exactly the
same pipeline as the API; `ask` adds the per-user document check.
"""

import logging
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import guardrails
from app.config import settings
from app.generation import (
    LLM,
    GenerationResult,
    detect_language,
    generate,
    leak_markers,
    refusal,
    rewrite_followup,
)
from app.models import Document, DocumentStatus, User
from app.retrieval import RetrievalConfig, RetrievalResult, RetrievedChunk, Retrievers, retrieve
from app.schemas import AskRequest, AskResponse, Citation, RetrievedChunkOut, Timings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AskOutcome:
    """Everything one question produced; the evaluation reads the extra fields.

    Attributes:
        response: What the API returns.
        standalone_question: The question after the follow-up rewrite.
        retrieval: What retrieval found (None if it did not run).
        generation: What generation produced (None if it was skipped).
    """

    response: AskResponse
    standalone_question: str
    retrieval: RetrievalResult | None
    generation: GenerationResult | None


async def ask(
    session: AsyncSession,
    user: User,
    request: AskRequest,
    retrievers: Retrievers,
    llm: LLM,
    request_id: str,
) -> AskResponse:
    """Answer a user's question from their own documents.

    Args:
        session: Database session.
        user: The user asking (only their chunks are searched).
        request: The question and optional history.
        retrievers: Embedders, reranker and Qdrant client.
        llm: The model client.
        request_id: Id of this request, echoed in the response.

    Returns:
        A cited answer or a refusal.

    Raises:
        UpstreamUnavailableError: If Qdrant or Anthropic stay unavailable (503).
    """
    started = time.perf_counter()
    if await count_ready_documents(session, user) == 0:
        # Say what is actually wrong instead of "I couldn't find this".
        return empty_index_response(request.question, request_id, _ms_since(started))
    history = [(turn.role, turn.content) for turn in request.history]
    outcome = await answer_question(
        request.question, history, user.id, retrievers, llm, request_id=request_id
    )
    return outcome.response


async def count_ready_documents(session: AsyncSession, user: User) -> int:
    """Count the user's documents that are searchable.

    Args:
        session: Database session.
        user: The owner.

    Returns:
        Documents with status ``ready``.
    """
    statement = select(func.count()).where(
        Document.owner_id == user.id, Document.status == DocumentStatus.READY
    )
    return int(await session.scalar(statement) or 0)


async def answer_question(
    question: str,
    history: list[tuple[str, str]],
    owner_id: uuid.UUID,
    retrievers: Retrievers,
    llm: LLM,
    *,
    request_id: str = "-",
    config: RetrievalConfig | None = None,
    collection: str | None = None,
) -> AskOutcome:
    """Run the full question-answering pipeline (no database needed).

    Args:
        question: The question as typed.
        history: Earlier ``(role, content)`` turns, oldest first (may be empty).
        owner_id: Whose chunks to search.
        retrievers: Embedders, reranker and Qdrant client.
        llm: The model client.
        request_id: Id of this request, echoed in the response.
        config: Retrieval steps (defaults to the final system).
        collection: Qdrant collection (defaults to the app's).

    Returns:
        The response plus the intermediate results.

    Raises:
        UpstreamUnavailableError: If Qdrant or Anthropic stay unavailable (503).
    """
    started = time.perf_counter()
    standalone = await rewrite_followup(question, history[-settings.history_max_turns :], llm)
    rewrite_ms = _ms_since(started)

    found = await retrieve(standalone, owner_id, retrievers, config, collection)

    generation_started = time.perf_counter()
    result: GenerationResult | None = None
    if found.below_threshold:
        # The documents almost certainly do not hold the answer: refuse
        # without paying for (or waiting on) an LLM call.
        answer = refusal(detect_language(question), "below_threshold")
    else:
        result = await generate(standalone, found.chunks, llm)
        answer = result
    generation_ms = _ms_since(generation_started)

    guard_started = time.perf_counter()
    answer = _check_leaks(answer)
    guardrails_ms = _ms_since(guard_started)

    timings = Timings(
        guardrails=guardrails_ms,
        retrieval=round(
            rewrite_ms + found.timings_ms.get("embed", 0) + found.timings_ms.get("search", 0), 1
        ),
        rerank=found.timings_ms.get("rerank", 0.0),
        generation=generation_ms,
        total=_ms_since(started),
    )
    response = build_response(answer, found.chunks, timings, request_id)
    return AskOutcome(
        response=response, standalone_question=standalone, retrieval=found, generation=result
    )


def _check_leaks(answer: GenerationResult) -> GenerationResult:
    """Replace an answer that leaks the prompt or a secret with a refusal.

    Args:
        answer: The generated answer.

    Returns:
        The same answer, or a refusal if a leak was found.
    """
    text = f"{answer.answer}\n{answer.general_context or ''}"
    leak = guardrails.check_output(text, leak_markers())
    if leak is None:
        return answer
    # Log the kind of leak only; logging the text would repeat the leak. The
    # log filter adds the request id to the line.
    logger.warning("Output guardrail replaced an answer (%s)", leak)
    return refusal(answer.language, f"leak_{leak}")


def build_response(
    answer: GenerationResult, chunks: list[RetrievedChunk], timings: Timings, request_id: str
) -> AskResponse:
    """Assemble the API response; citations come from chunk metadata only.

    Args:
        answer: Generated answer or refusal.
        chunks: The retrieved chunks, numbered from 1 as in the prompt.
        timings: Time per stage.
        request_id: Id of this request.

    Returns:
        The `AskResponse`.
    """
    citations = [
        Citation(
            n=ref.n,
            chunk_id=ref.chunk.chunk_id,
            doc_id=ref.chunk.doc_id,
            filename=ref.chunk.filename,
            page_start=ref.chunk.page_start,
            page_end=ref.chunk.page_end,
            section_path=list(ref.chunk.section_path),
        )
        for ref in answer.citations
    ]
    retrieved = [
        RetrievedChunkOut(
            n=n,
            chunk_id=chunk.chunk_id,
            filename=chunk.filename,
            page_start=chunk.page_start,
            section_path=list(chunk.section_path),
            text=chunk.text,
            similarity=round(chunk.similarity, 4),
            rerank_score=None if chunk.rerank_score is None else round(chunk.rerank_score, 4),
        )
        for n, chunk in enumerate(chunks, start=1)
    ]
    return AskResponse(
        request_id=request_id,
        answer=answer.answer,
        answerable=answer.answerable,
        language=answer.language,
        citations=citations,
        general_context=answer.general_context,
        chunk_count=len(chunks),
        retrieved_chunks=retrieved,
        timings_ms=timings,
    )


def empty_index_response(question: str, request_id: str, elapsed_ms: float) -> AskResponse:
    """Build the response for a user with no searchable documents.

    Args:
        question: The question (for its language).
        request_id: Id of this request.
        elapsed_ms: Time spent so far.

    Returns:
        A not-answerable response with the empty-index message.
    """
    return AskResponse(
        request_id=request_id,
        answer=settings.empty_index_message,
        answerable=False,
        language=detect_language(question),
        citations=[],
        general_context=None,
        chunk_count=0,
        retrieved_chunks=[],
        timings_ms=Timings(
            guardrails=0.0, retrieval=0.0, rerank=0.0, generation=0.0, total=elapsed_ms
        ),
    )


def _ms_since(started: float) -> float:
    """Milliseconds since a ``time.perf_counter()`` reading, rounded to 0.1."""
    return round((time.perf_counter() - started) * 1000, 1)
