"""Pydantic request and response models (the API's public contract)."""

import uuid
from datetime import datetime
from http import HTTPStatus
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, StringConstraints


class ErrorDetail(BaseModel):
    """The inside of every error response.

    Attributes:
        code: Stable machine-readable error name.
        message: Human-readable explanation.
        request_id: Matches the ``X-Request-ID`` header and the server log line.
    """

    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    """The one error shape used by every endpoint, including 422 and 500."""

    error: ErrorDetail

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "error": {
                        "code": "document_not_found",
                        "message": "Document not found.",
                        "request_id": "3f2b9c0e8a7d4e61b1c2d3e4f5a6b7c8",
                    }
                }
            ]
        }
    )


CheckStatus = Literal["ok", "error"]


class HealthChecks(BaseModel):
    """Result of each dependency check.

    Attributes:
        database: Neon answered ``SELECT 1`` in time.
        qdrant: Qdrant listed its collections in time.
    """

    database: CheckStatus
    qdrant: CheckStatus


class HealthResponse(BaseModel):
    """Body of GET /health: overall status plus each dependency's status."""

    status: CheckStatus
    checks: HealthChecks

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"status": "ok", "checks": {"database": "ok", "qdrant": "ok"}},
                {"status": "error", "checks": {"database": "ok", "qdrant": "error"}},
            ]
        }
    )


# --- Auth ---------------------------------------------------------------------

# Emails are case-insensitive in practice, so store one canonical form; otherwise
# "Ali@x.com" and "ali@x.com" could become two accounts.
NormalizedEmail = Annotated[EmailStr, AfterValidator(str.lower)]
FullName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class UserCreate(BaseModel):
    """Body of POST /auth/signup.

    Attributes:
        email: Login name; must be a valid address, stored in lower case.
        full_name: Display name, 1-200 characters after trimming spaces.
        password: 8-128 characters. The upper limit stops someone sending a
            huge string to make the (deliberately slow) argon2 hash even slower.
    """

    email: NormalizedEmail
    full_name: FullName
    password: str = Field(min_length=8, max_length=128)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "email": "developer@example.com",
                    "full_name": "Ayesha Khan",
                    "password": "a-long-secret-phrase",
                }
            ]
        }
    )


class UserRead(BaseModel):
    """A user as returned by the API; never includes the password hash.

    Attributes:
        id: The user's UUID.
        email: Login email (lower case).
        full_name: Display name.
        created_at: When the account was created.
    """

    id: uuid.UUID
    email: str
    full_name: str
    created_at: datetime

    # from_attributes=True lets FastAPI build this straight from a `User` ORM row.
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "0b6f3c5e-2a4d-4c1e-9f7a-8d2e1b3c4a5f",
                    "email": "developer@example.com",
                    "full_name": "Ayesha Khan",
                    "created_at": "2026-11-19T10:15:00Z",
                }
            ]
        },
    )


class Token(BaseModel):
    """Body of a successful POST /auth/login (the OAuth2 token response).

    Attributes:
        access_token: The signed JWT to send as ``Authorization: Bearer <token>``.
        token_type: Always ``bearer``; the OAuth2 spec and Swagger expect this field.
    """

    access_token: str
    token_type: Literal["bearer"] = "bearer"

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    "token_type": "bearer",
                }
            ]
        }
    )


# --- Documents ----------------------------------------------------------------

DocumentStatusName = Literal["queued", "processing", "ready", "failed"]


class DocumentRead(BaseModel):
    """A document as returned by the API (never the file path or owner id).

    Attributes:
        id: The document's UUID; use it in GET/DELETE /documents/{id}.
        filename: Original file name, as shown in citations.
        file_type: pdf, docx, pptx, txt or md (checked from the content).
        size_bytes: File size.
        status: ``ready`` when searchable; ``processing`` while a large file is
            still being embedded (poll this document); ``failed`` with ``error``.
        pages: Pages or slides; null for DOCX, TXT and MD.
        chunk_count: Chunks stored in the vector database.
        ocr_pages: Pages read by OCR or image captioning.
        warnings: Non-fatal notes, e.g. instruction-like text was found.
        error: Why ingestion failed, when ``status`` is ``failed``.
        created_at: Upload time.
        updated_at: Last status change.
    """

    id: uuid.UUID
    filename: str
    file_type: str
    size_bytes: int
    status: DocumentStatusName
    pages: int | None
    chunk_count: int
    ocr_pages: int
    warnings: list[str]
    error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "5d1f8a2b-3c4e-4f60-8a9b-0c1d2e3f4a5b",
                    "filename": "sro_69(I)_2025_digital_invoicing_rules.pdf",
                    "file_type": "pdf",
                    "size_bytes": 1843220,
                    "status": "ready",
                    "pages": 11,
                    "chunk_count": 26,
                    "ocr_pages": 0,
                    "warnings": [],
                    "error": None,
                    "created_at": "2026-11-20T09:15:00Z",
                    "updated_at": "2026-11-20T09:15:41Z",
                },
                {
                    "id": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
                    "filename": "sales_tax_rules_2006_upto_2026-07-31.pdf",
                    "file_type": "pdf",
                    "size_bytes": 2950112,
                    "status": "processing",
                    "pages": 232,
                    "chunk_count": 0,
                    "ocr_pages": 0,
                    "warnings": [],
                    "error": None,
                    "created_at": "2026-11-20T09:20:00Z",
                    "updated_at": "2026-11-20T09:21:05Z",
                },
            ]
        },
    )


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """Build a route's ``responses=`` entry so Swagger shows its error codes.

    Args:
        *status_codes: The error statuses the route can return, e.g. 401, 404.

    Returns:
        A mapping of status code to the shared `ErrorResponse` model.
    """
    return {
        code: {"model": ErrorResponse, "description": HTTPStatus(code).phrase}
        for code in status_codes
    }


# --- Ask ----------------------------------------------------------------------

# Trimmed first, so "   " counts as empty and is rejected with 422.
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class HistoryTurn(BaseModel):
    """One earlier message of the conversation, sent with a follow-up question.

    Attributes:
        role: Who wrote it.
        content: What was said (answers are long, so the cap is higher than a question's).
    """

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


# Named request examples. Swagger shows them as a dropdown on POST /ask
# (`openapi_examples` in routes/ask.py); the schema lists the same values.
ASK_EXAMPLES: dict[str, dict[str, Any]] = {
    "english": {
        "summary": "English question",
        "value": {"question": "How long can the Commissioner extend the integration date?"},
    },
    "urdu": {
        "summary": "Urdu question (answered in Urdu)",
        "value": {"question": "کمشنر انٹیگریشن کی تاریخ کتنی بڑھا سکتا ہے؟"},
    },
    "follow_up": {
        "summary": "Follow-up with history",
        "value": {
            "question": "And what happens if I miss it?",
            "history": [
                {
                    "role": "user",
                    "content": "How long can the Commissioner extend the integration date?",
                },
                {
                    "role": "assistant",
                    "content": "Up to 60 days in total, in 15-day steps [1].",
                },
            ],
        },
    },
}


class AskRequest(BaseModel):
    """Body of POST /ask.

    Attributes:
        question: 1-1000 characters after trimming spaces.
        history: Up to the last 6 turns, oldest first; only used to rewrite a
            follow-up into a standalone question.
    """

    question: Question
    history: list[HistoryTurn] = Field(default_factory=list, max_length=6)

    model_config = ConfigDict(
        json_schema_extra={"examples": [example["value"] for example in ASK_EXAMPLES.values()]}
    )


class Citation(BaseModel):
    """A source of the answer, built by code from the chunk's metadata.

    Attributes:
        n: The number used as ``[n]`` in the answer.
        chunk_id: The chunk in the search index.
        doc_id: The document it belongs to.
        filename: Document file name.
        page_start: First page (or slide) of the passage.
        page_end: Last page of the passage.
        section_path: Headings above the passage, outermost first.
    """

    n: int
    chunk_id: str
    doc_id: str
    filename: str
    page_start: int
    page_end: int
    section_path: list[str]


class RetrievedChunkOut(BaseModel):
    """A chunk shown in the "retrieved chunks" panel.

    Attributes:
        n: Its document number in the prompt.
        chunk_id: The chunk in the search index.
        filename: Document file name.
        page_start: First page of the chunk.
        section_path: Headings above the chunk.
        text: The chunk text.
        similarity: Cosine similarity between question and chunk (-1 to 1).
        rerank_score: Cross-encoder score (0-1), or null when reranking is off.
    """

    n: int
    chunk_id: str
    filename: str
    page_start: int
    section_path: list[str]
    text: str
    similarity: float
    rerank_score: float | None


class Timings(BaseModel):
    """Milliseconds spent in each stage of the request.

    Attributes:
        guardrails: Input and output checks.
        retrieval: Follow-up rewrite, query embedding and search.
        rerank: Cross-encoder reranking and boosts.
        generation: The answering model call(s) and citation check.
        total: The whole pipeline.
    """

    guardrails: float
    retrieval: float
    rerank: float
    generation: float
    total: float


class AskResponse(BaseModel):
    """Body of a successful POST /ask: an answer with citations, or a refusal.

    Attributes:
        request_id: Matches the ``X-Request-ID`` header and the log line.
        answer: The answer with ``[n]`` markers, or the refusal sentence.
        answerable: False when the documents do not contain the answer.
        language: ISO 639-1 code of the answer.
        citations: The sources behind the ``[n]`` markers.
        general_context: Background from general knowledge; never cited.
        chunk_count: How many chunks the answer was generated from.
        retrieved_chunks: Those chunks, best first.
        timings_ms: Time per stage.
    """

    request_id: str
    answer: str
    answerable: bool
    language: str
    citations: list[Citation]
    general_context: str | None
    chunk_count: int
    retrieved_chunks: list[RetrievedChunkOut]
    timings_ms: Timings

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "request_id": "3f2b9c0e8a7d4e61b1c2d3e4f5a6b7c8",
                    "answer": (
                        "Yes. The Commissioner Inland Revenue may extend the integration date "
                        "for up to 60 days in aggregate, 15 days at a time [1].\n\n"
                        "This is not legal advice; check the cited rule."
                    ),
                    "answerable": True,
                    "language": "en",
                    "citations": [
                        {
                            "n": 1,
                            "chunk_id": "7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f",
                            "doc_id": "5d1f8a2b-3c4e-4f60-8a9b-0c1d2e3f4a5b",
                            "filename": "sales_tax_rules_2006_upto_2026-07-31.pdf",
                            "page_start": 108,
                            "page_end": 108,
                            "section_path": ["Chapter XIV-H", "150V. Extension of time"],
                        }
                    ],
                    "general_context": None,
                    "chunk_count": 6,
                    "retrieved_chunks": [
                        {
                            "n": 1,
                            "chunk_id": "7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f",
                            "filename": "sales_tax_rules_2006_upto_2026-07-31.pdf",
                            "page_start": 108,
                            "section_path": ["Chapter XIV-H", "150V. Extension of time"],
                            "text": "150V. ... extension ... up to sixty days in aggregate.",
                            "similarity": 0.71,
                            "rerank_score": 0.93,
                        }
                    ],
                    "timings_ms": {
                        "guardrails": 0.4,
                        "retrieval": 820.5,
                        "rerank": 14250.0,
                        "generation": 3900.2,
                        "total": 18971.1,
                    },
                },
                {
                    "request_id": "1b2c3d4e5f6a4b7c8d9e0f1a2b3c4d5e",
                    "answer": (
                        "کمشنر ان لینڈ ریونیو انٹیگریشن کی تاریخ مجموعی طور پر 60 دن تک، "
                        "15 دن کے وقفوں سے بڑھا سکتا ہے [1]۔\n\n"
                        "یہ قانونی مشورہ نہیں ہے؛ حوالہ دیا گیا قاعدہ خود دیکھ لیں۔"
                    ),
                    "answerable": True,
                    "language": "ur",
                    "citations": [
                        {
                            "n": 1,
                            "chunk_id": "7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f",
                            "doc_id": "5d1f8a2b-3c4e-4f60-8a9b-0c1d2e3f4a5b",
                            "filename": "sales_tax_rules_2006_upto_2026-07-31.pdf",
                            "page_start": 108,
                            "page_end": 108,
                            "section_path": ["Chapter XIV-H", "150V. Extension of time"],
                        }
                    ],
                    "general_context": None,
                    "chunk_count": 6,
                    "retrieved_chunks": [],
                    "timings_ms": {
                        "guardrails": 0.5,
                        "retrieval": 850.0,
                        "rerank": 14100.0,
                        "generation": 5200.0,
                        "total": 20150.5,
                    },
                },
                {
                    "request_id": "9a8b7c6d5e4f4a3b8c2d1e0f9a8b7c6d",
                    "answer": "I couldn't find this in your documents.",
                    "answerable": False,
                    "language": "en",
                    "citations": [],
                    "general_context": None,
                    "chunk_count": 0,
                    "retrieved_chunks": [],
                    "timings_ms": {
                        "guardrails": 0.3,
                        "retrieval": 790.0,
                        "rerank": 0.0,
                        "generation": 0.0,
                        "total": 790.3,
                    },
                },
            ]
        }
    )
