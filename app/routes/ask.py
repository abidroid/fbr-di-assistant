"""Question endpoint: ask about your own documents."""

from fastapi import APIRouter, Request, status

from app.deps import CurrentUser, LLMDep, RetrieversDep, SessionDep
from app.schemas import AskRequest, AskResponse, error_responses
from app.services import ask_service

router = APIRouter(tags=["ask"])


@router.post(
    "/ask",
    summary="Ask a question about your documents",
    description=(
        "Answers from your own uploaded documents only, citing every claim as `[n]`; each "
        "`n` is listed in `citations` with file, pages and section. If the documents do not "
        "contain the answer, `answerable` is false and `answer` is exactly "
        '"I couldn\'t find this in your documents."\n\n'
        'Send up to the last 6 turns in `history` for a follow-up such as "and for credit '
        'notes?"; it is rewritten into a standalone question before searching.\n\n'
        "`retrieved_chunks` shows what the answer was generated from, with cosine similarity "
        "and rerank score; `timings_ms` shows where the time went."
    ),
    response_model=AskResponse,
    status_code=status.HTTP_200_OK,
    responses=error_responses(400, 401, 422, 429, 503),
)
async def ask(
    body: AskRequest,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    retrievers: RetrieversDep,
    llm: LLMDep,
) -> AskResponse:
    """Answer a question from the user's documents.

    Args:
        body: The question and optional history.
        request: The HTTP request (for its request id).
        session: Database session for this request.
        user: The logged-in user.
        retrievers: Embedders, reranker and Qdrant client.
        llm: The model client.

    Returns:
        The answer with citations, or a refusal.
    """
    request_id = getattr(request.state, "request_id", "-")
    return await ask_service.ask(session, user, body, retrievers, llm, request_id)
