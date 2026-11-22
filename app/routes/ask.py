"""Question endpoint: ask about your own documents."""

from typing import Annotated

from fastapi import APIRouter, Body, Request, status

from app.config import settings
from app.deps import ClassifierDep, CurrentUser, LLMDep, RetrieversDep, SessionDep
from app.guardrails import limiter
from app.schemas import ASK_EXAMPLES, AskRequest, AskResponse, error_responses
from app.services import ask_service

router = APIRouter(tags=["ask"])


@router.post(
    "/ask",
    summary="Ask a question about your documents",
    description=(
        "Answers from your own uploaded documents only, citing every claim as `[n]`; each "
        "`n` is listed in `citations` with file, pages and section. If the documents do not "
        "contain the answer, `answerable` is false and `answer` is exactly "
        '"I couldn\'t find this in your documents." (in Urdu for an Urdu question).\n\n'
        "Ask in English or Urdu: the answer, refusal and disclaimer come back in the "
        "question's language (`language` is `en` or `ur`), while codes and field names stay "
        "as written in the documents.\n\n"
        'Send up to the last 6 turns in `history` for a follow-up such as "and for credit '
        'notes?"; it is rewritten into a standalone question before searching.\n\n'
        "`retrieved_chunks` shows what the answer was generated from, with cosine similarity "
        "and rerank score; `timings_ms` shows where the time went.\n\n"
        "Questions that try to override the assistant's instructions get 400; questions "
        "unrelated to documents get a polite refusal (200, `answerable` false). Each user may "
        "ask `RATE_LIMIT` questions per window (default 20 per minute), then gets 429."
    ),
    response_model=AskResponse,
    status_code=status.HTTP_200_OK,
    responses=error_responses(400, 401, 422, 429, 503),
)
# A callable, so the limit is read per request and tests can lower it.
@limiter.limit(lambda: settings.rate_limit)
async def ask(
    # openapi_examples gives Swagger a dropdown (English, Urdu, follow-up).
    body: Annotated[AskRequest, Body(openapi_examples=ASK_EXAMPLES)],
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    retrievers: RetrieversDep,
    llm: LLMDep,
    classifier: ClassifierDep,
) -> AskResponse:
    """Answer a question from the user's documents.

    Args:
        body: The question and optional history.
        request: The HTTP request (request id; slowapi needs it too).
        session: Database session for this request.
        user: The logged-in user.
        retrievers: Embedders, reranker and Qdrant client.
        llm: The model client.
        classifier: The input guardrail classifier.

    Returns:
        The answer with citations, or a refusal.
    """
    request_id = getattr(request.state, "request_id", "-")
    return await ask_service.ask(session, user, body, retrievers, llm, classifier, request_id)
