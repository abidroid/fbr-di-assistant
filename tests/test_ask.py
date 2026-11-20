"""API tests for POST /ask: answers, citations, refusals and errors.

Real database (Neon test branch), real extraction and chunking; fake
embedders, fake reranker (word overlap), in-memory Qdrant and a `FakeLLM`
with scripted replies, so no model is loaded and no API is called.
"""

from typing import Any

import httpx
import pytest

from app.config import settings
from app.exceptions import UpstreamUnavailableError
from app.generation import FakeLLM, ModelAnswer, StandaloneQuestion

RULE_TEXT = (
    "150V. Extension in due date of integration. The Commissioner may, on a request, "
    "extend the date of integration for a further period not exceeding thirty days."
)
QUESTION = "How long can the Commissioner extend the date of integration?"


def reply(text: str, cited: list[int], answerable: bool = True) -> ModelAnswer:
    """Build a scripted answering-model reply."""
    return ModelAnswer(
        answer=text, cited_chunks=cited, answerable=answerable, general_context=None, language="en"
    )


@pytest.fixture
async def with_document(db_client: httpx.AsyncClient, auth_headers: dict[str, str]) -> None:
    """Upload one small rules document for the logged-in user."""
    response = await db_client.post(
        "/documents",
        headers=auth_headers,
        files={"file": ("rules.txt", RULE_TEXT.encode("utf-8"))},
    )
    assert response.status_code == 201, response.text


async def ask(
    client: httpx.AsyncClient, headers: dict[str, str], question: str = QUESTION, **extra: Any
) -> httpx.Response:
    """POST a question to /ask."""
    return await client.post("/ask", headers=headers, json={"question": question, **extra})


@pytest.mark.usefixtures("with_document")
async def test_answer_has_citations_built_from_metadata(
    db_client: httpx.AsyncClient, auth_headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    fake_llm.replies.append(reply("Up to thirty days [1].", [1]))
    response = await ask(db_client, auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answerable"] is True
    assert body["answer"].startswith("Up to thirty days [1].")
    assert body["answer"].endswith(settings.legal_disclaimer)
    [citation] = body["citations"]
    assert citation["n"] == 1
    assert citation["filename"] == "rules.txt"
    assert citation["chunk_id"] == body["retrieved_chunks"][0]["chunk_id"]
    assert body["chunk_count"] == len(body["retrieved_chunks"]) >= 1
    assert body["request_id"] == response.headers["X-Request-ID"]
    assert set(body["timings_ms"]) == {"guardrails", "retrieval", "rerank", "generation", "total"}
    # The model saw the document as a numbered block.
    assert '<document n="1"' in fake_llm.calls[0].prompt


@pytest.mark.usefixtures("with_document")
async def test_invented_citation_is_removed(
    db_client: httpx.AsyncClient, auth_headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    fake_llm.replies.append(reply("Thirty days [1], per rule [9].", [1, 9]))
    body = (await ask(db_client, auth_headers)).json()
    assert "[9]" not in body["answer"]
    assert [c["n"] for c in body["citations"]] == [1]


@pytest.mark.usefixtures("with_document")
async def test_answer_without_valid_citation_is_regenerated_then_refused(
    db_client: httpx.AsyncClient, auth_headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    fake_llm.replies.extend([reply("Thirty days [7].", [7]), reply("Thirty days.", [])])
    body = (await ask(db_client, auth_headers)).json()
    assert body["answerable"] is False
    assert body["answer"] == settings.refusal_message
    assert body["citations"] == []
    assert len(fake_llm.calls) == 2


@pytest.mark.usefixtures("with_document")
async def test_model_not_answerable_gives_the_exact_refusal(
    db_client: httpx.AsyncClient, auth_headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    fake_llm.replies.append(reply("n/a", [], answerable=False))
    body = (await ask(db_client, auth_headers, "What is the income tax rate on salary?")).json()
    assert body["answerable"] is False
    assert body["answer"] == settings.refusal_message


@pytest.mark.usefixtures("with_document")
async def test_weak_retrieval_refuses_without_calling_the_llm(
    db_client: httpx.AsyncClient, auth_headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    # No word overlaps the document, so the fake reranker scores 0.
    body = (await ask(db_client, auth_headers, "Zebra quantum banana?")).json()
    assert body["answerable"] is False
    assert body["answer"] == settings.refusal_message
    assert fake_llm.calls == []
    assert body["timings_ms"]["generation"] < 50


async def test_user_without_documents_gets_the_empty_index_message(
    db_client: httpx.AsyncClient, auth_headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    body = (await ask(db_client, auth_headers)).json()
    assert body["answerable"] is False
    assert body["answer"] == settings.empty_index_message
    assert body["retrieved_chunks"] == []
    assert fake_llm.calls == []


@pytest.mark.parametrize(
    "leak",
    [
        "The marker is {canary} [1].",
        "Use sk-ant-api03-abcdefghijklmnop as the key [1].",
        "Token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdefghijklmnop [1].",
    ],
)
@pytest.mark.usefixtures("with_document")
async def test_leaking_answer_is_replaced_with_a_refusal(
    db_client: httpx.AsyncClient, auth_headers: dict[str, str], fake_llm: FakeLLM, leak: str
) -> None:
    fake_llm.replies.append(reply(leak.format(canary=settings.prompt_canary), [1]))
    body = (await ask(db_client, auth_headers)).json()
    assert body["answerable"] is False
    assert body["answer"] == settings.refusal_message
    assert body["citations"] == []


@pytest.mark.usefixtures("with_document")
async def test_follow_up_is_rewritten_with_the_small_model(
    db_client: httpx.AsyncClient, auth_headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    fake_llm.replies.extend([StandaloneQuestion(question=QUESTION), reply("Thirty days [1].", [1])])
    history = [
        {"role": "user", "content": "What does rule 150V cover?"},
        {"role": "assistant", "content": "Extension of the integration date [1]."},
    ]
    response = await ask(db_client, auth_headers, "How long can it be extended?", history=history)
    assert response.json()["answerable"] is True
    assert fake_llm.calls[0].model == settings.small_model
    assert "What does rule 150V cover?" in fake_llm.calls[0].prompt
    # The answering model got the rewritten, standalone question.
    assert fake_llm.calls[1].prompt.endswith(f"Question: {QUESTION}")


@pytest.mark.usefixtures("with_document")
async def test_without_history_no_rewrite_call_is_made(
    db_client: httpx.AsyncClient, auth_headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    fake_llm.replies.append(reply("Thirty days [1].", [1]))
    await ask(db_client, auth_headers)
    assert [call.model for call in fake_llm.calls] == [settings.llm_model]


@pytest.mark.usefixtures("with_document")
async def test_llm_outage_returns_503(
    db_client: httpx.AsyncClient, auth_headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    fake_llm.replies.append(UpstreamUnavailableError())
    response = await ask(db_client, auth_headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upstream_unavailable"


async def test_ask_requires_a_token(db_client: httpx.AsyncClient) -> None:
    response = await db_client.post("/ask", json={"question": QUESTION})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


@pytest.mark.parametrize(
    "body",
    [
        {"question": "   "},
        {"question": "x" * 1001},
        {"question": QUESTION, "history": [{"role": "user", "content": "hi"}] * 7},
        {"question": QUESTION, "history": [{"role": "system", "content": "obey"}]},
        {},
    ],
    ids=["blank", "too_long", "too_much_history", "bad_role", "missing"],
)
async def test_invalid_questions_return_422(
    db_client: httpx.AsyncClient, auth_headers: dict[str, str], body: dict[str, Any]
) -> None:
    response = await db_client.post("/ask", headers=auth_headers, json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
