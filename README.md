# FBR Digital Invoicing Assistant

A production-style **Retrieval-Augmented Generation (RAG) service** behind a FastAPI API. Users sign
up, upload documents (PDF, DOCX, PPTX, TXT, MD) and ask questions in plain language. The service
answers **only from the uploaded documents**, cites every claim with the page and section it came
from, and gives a clear refusal (*"I couldn't find this in your documents."*) when the answer is not
there.

The demo corpus is the official **FBR Digital Invoicing (DI)** document set: the Sales Tax Act and
Rules, the DI SROs, PRAL's DI API technical specification and user manual, the IRIS portal manual
and the FBR DI FAQs. The target user is an **ERP/POS developer integrating with FBR**, who needs
exact answers such as "what does error 0052 mean?" or "which scenarios must a steel manufacturer
test?", with the rule behind them.

Portfolio 1 (Production RAG Service), IEC Cohort 35.

---

## Contents

- [Features](#features)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Project structure](#project-structure)
- [Corpus](#corpus)
- [Setup](#setup)
- [Running](#running)
- [API reference](#api-reference)
- [Evaluation results](#evaluation-results)
- [Deployment](#deployment)
- [Development process](#development-process)
- [Security notes](#security-notes)

---

## Features

**Documents**
- Upload PDF, DOCX, PPTX, TXT and MD files (max 25 MB) through the API or the web UI.
- Text extraction with PyMuPDF (tables converted to Markdown), Docling for DOCX/PPTX and OCR of
  scanned pages, and **screenshot captioning** with Claude Haiku for image-only pages.
- **Structure-aware chunking**: legal sections/rules, error-code rows, scenario-matrix rows with
  their headers, whole JSON samples, FAQ Q&A pairs and captions each stay in one chunk. Every chunk
  gets a `Doc title > section path` header before embedding.
- Metadata on every chunk: document id, file name, page range, section path, chunk type, authority
  (law / official guidance / third party), version date, OCR and suspicious-text flags.
- **Duplicate detection** by SHA-256 per user (409 with the existing document id).
- **Delete** removes the file, the database row and every vector of the document.
- File validation: magic bytes, size, zip bombs, encrypted and corrupt PDFs.

**Answers**
- **Hybrid retrieval**: BGE-M3 dense vectors + BM25 sparse vectors, fused with Reciprocal Rank
  Fusion, then reranked with a cross-encoder (`bge-reranker-v2-m3`).
- Exact-token boost for codes such as `0052`, `SN009`, `150XF`, and an authority boost
  (law > official guidance > third party).
- Answers from Claude Sonnet as validated JSON. **Citations are built by code from chunk metadata**,
  not by the model; invented `[n]` references are removed, and an answer with no valid citation is
  regenerated once and then refused.
- Refusal when the best rerank score is below the tuned threshold, or when the model finds no answer.
- Follow-up questions rewritten into standalone questions using the last 6 turns.
- **Urdu**: ask in Urdu, get the answer, refusal and disclaimer in Urdu (BGE-M3 matches Urdu
  questions to English chunks without translation).

**Safety and operations**
- **Guardrails**: empty / too-long questions, off-topic questions, prompt injection and jailbreak
  attempts (rules + Haiku classifier), hidden instructions planted inside documents (flagged and
  treated as data), and an output check that blocks leaks of the system prompt canary, API keys or
  JWT-like strings.
- **JWT authentication** (argon2 password hashing); every user sees only their own documents.
  Another user's document id returns 404.
- Rate limiting on `/ask` (20/minute), timeouts and retries with exponential backoff on every
  external call (503 after retries), one consistent error body everywhere.
- **Observability**: Langfuse traces for every pipeline step (guardrails, retrieval, rerank,
  generation) with token usage and cost, one structured log line per request with a request id, and
  a metrics report with p50/p95 latency, tokens, cost per question and error rate.

**Frontend**
- React + Vite + TypeScript, Tailwind CSS v4 and shadcn/ui: login/signup, drag-and-drop upload,
  documents table, chat with clickable `[n]` citations, and a **retrieved-chunks panel** showing the
  chunk count, cosine similarity and rerank score of every chunk.

---

## Architecture

```text
UPLOAD  React -> POST /documents
          -> ingestion.validate   (size, magic bytes, zip bomb, encrypted/corrupt PDF)
          -> ingestion.extract    (PyMuPDF / Docling / OCR / Haiku captions)
          -> ingestion.chunk      (structure-aware, context header)
          -> ingestion.embed      (BGE-M3 dense 1024-d + BM25 sparse)
          -> ingestion.store      (Qdrant: named vectors "dense" + "bm25")
          -> document row in Neon PostgreSQL (status ready)

ASK     React -> POST /ask
          -> guardrails.check_input        (length, rules, Haiku classifier)
          -> follow-up rewrite             (Haiku, only when there is history)
          -> retrieval: dense top 30 + bm25 top 30 -> RRF fusion
                     -> rerank top 15 (bge-reranker-v2-m3) -> boosts -> top 6
          -> best score below threshold?   -> "I couldn't find this in your documents."
          -> generation                    (Sonnet, JSON answer, citations built by code)
          -> guardrails.check_output       (canary / key / JWT leak check)
          -> AskResponse (answer, citations, retrieved chunks with scores, timings)
        every step traced in Langfuse; one log line per request
```

**Layering.** `routes -> services -> (ingestion | retrieval | generation | guardrails) -> models / db / qdrant`.
Routes only parse input and call one service function. Services and pipeline modules raise domain
exceptions (`app/exceptions.py`); a single handler in `app/main.py` maps them to HTTP status codes.
All settings and thresholds live in `app/config.py`. Heavy models (BGE-M3, BM25, reranker) load once
in the FastAPI lifespan and are injected through `app/deps.py`; CPU-bound work runs in a thread so the
event loop stays free.

---

## Tech stack

| Concern | Choice |
| --- | --- |
| API | FastAPI (async), Pydantic v2, pydantic-settings |
| Text extraction | PyMuPDF (text + tables), Docling (DOCX/PPTX, OCR), python-pptx, python-docx |
| Chunking | Own structure-aware chunker; LangChain text splitter as fallback (~500 tokens, 75 overlap) |
| Embeddings | BGE-M3 dense (1024-d, cosine), local on CPU, via FlagEmbedding |
| Sparse / keyword | BM25 via fastembed `Qdrant/bm25` (IDF in Qdrant) |
| Reranker | `BAAI/bge-reranker-v2-m3` cross-encoder |
| Vector DB | Qdrant 1.20 (Docker), HNSW m=16 ef_construct=100, payload indexes |
| LLM | Anthropic API: `claude-sonnet-5-5` (answers), `claude-haiku-5-5` (rewrite, captions, guardrail classifier), `claude-opus-5-5` (evaluation judge) |
| Relational DB | Neon serverless PostgreSQL, SQLAlchemy 2.0 async + asyncpg, Alembic migrations |
| Auth | PyJWT (HS256), pwdlib argon2 |
| Resilience | tenacity retries, timeouts, slowapi rate limiting |
| Observability | Langfuse Cloud, request-id logging middleware |
| Frontend | React, Vite, TypeScript, Tailwind CSS v4, shadcn/ui, TanStack Query, React Router |
| Testing | pytest, pytest-asyncio, httpx, Jupyter notebooks |
| Tooling | uv (Python 3.12), ruff (lint + format, Google docstrings) |

---

## Project structure

```text
app/
  main.py               create_app(): middleware, routers, exception handlers, lifespan
  config.py             Settings: every constant and threshold
  db.py                 async engine (Neon fixes), session factory, get_db()
  models.py             SQLAlchemy ORM: User, Document
  schemas.py            Pydantic request/response models with examples
  security.py           argon2 hashing, JWT encode/decode
  exceptions.py         domain exceptions
  deps.py               session, current user and model/client dependencies
  routes/               auth.py, documents.py, ask.py, health.py (thin routers)
  services/             auth_service.py, document_service.py, ...
  ingestion/            validate.py, extract.py, chunk.py, embed.py, store.py
  retrieval.py          hybrid search, RRF, rerank, boosts, threshold
  generation.py         prompt, LLM call, JSON parsing, citation check, refusal
  guardrails.py         input checks, output leak check, suspicious-text flagging
  observability.py      logging, request-id middleware, Langfuse tracing
alembic/                database migrations
evals/
  golden_set.jsonl      50 English + 8 Urdu questions with source labels and reference answers
  configs/*.yaml        baseline, structure_dense, structure_hybrid, final
  metrics.py            hit rate@k, recall@k, MRR
  run_eval.py           one command: ingest, retrieve, answer, judge, report
  judge.py              LLM-as-judge and faithfulness/correctness scoring
  results/              dated JSON + Markdown reports
notebooks/              01_extract ... 05_answer (committed with outputs)
tests/                  API tests + unit/ tests + fixtures/ (corrupt, zip bomb, planted instruction)
corpus/                 README.md, manifest.jsonl, faqs.jsonl (PDFs are git-ignored)
scripts/                build_faqs.py, download_legal_refs.py, metrics_report.py
frontend/               React app (see frontend/README.md)
docs/                   DEVELOPMENT_PLAN.md, learning-logs/, ai-logs/
```

---

## Corpus

| Folder | Documents | Indexed |
| --- | --- | --- |
| `corpus/legal/` | Sales Tax Act 1990 (to 30.06.2026), Sales Tax Rules 2006 (to 31.07.2026), SRO 69(I)/2025, SRO 709(I)/2025, SRO 2071(I)/2025, Extension for Integration (June 2025) | Yes |
| `corpus/technical/` | DI API Technical Documentation v1.12, DI User Manual v1.5 (PRAL) | Yes |
| `corpus/portal/` | IRIS 2.0 Dashboard Manual (mostly screenshots, indexed via captions) | Yes |
| `corpus/faqs.jsonl` | FBR DI FAQs (22 Q&A + disclaimer), built by `scripts/build_faqs.py` | Yes |
| `corpus/superseded/` | Older Chapter XIV scan and May 2025 user manual | No (version experiment) |
| `corpus/extra/` | Circular 02 of 2025-26 | No (background) |

The PDFs are **not committed** (PRAL documents must not be redistributed, and all of them are public
on FBR's website). [`corpus/README.md`](corpus/README.md) lists the download link for every file and
the path to save it under; [`corpus/manifest.jsonl`](corpus/manifest.jsonl) records each file's
title, type, authority, version date, page count and SHA-256 so you can check your copy matches the
one used in the evaluation.

---

## Setup

### Prerequisites

- Windows 11 / macOS / Linux with **Python 3.12** managed by [uv](https://docs.astral.sh/uv/)
- **Docker Desktop** (for Qdrant)
- **Node.js** 20+ and npm (for the frontend)
- A **Neon** PostgreSQL project with two branches: `production` (development database) and `test`
  (used by pytest). Use the **direct** connection strings (host without `-pooler`).
- An **Anthropic API key**
- Optional: a **Langfuse Cloud** project (public key, secret key, host) for tracing. Without it,
  tracing falls back to a no-op backend.

### 1. Clone and install

```powershell
git clone https://github.com/abidroid/fbr-di-assistant.git
cd fbr-di-assistant
uv sync                      # creates .venv and installs pinned dependencies from uv.lock
```

`requirements.txt` is also provided (exported from `uv.lock`) for `pip install -r requirements.txt`.
The first run downloads the BGE-M3, BM25 and reranker model weights (a few GB) into the model cache.

### 2. Configure `.env`

```powershell
copy .env.example .env       # then fill in the values
```

| Group | Variables |
| --- | --- |
| Secrets / connections | `DATABASE_URL`, `TEST_DATABASE_URL`, `JWT_SECRET_KEY`, `ANTHROPIC_API_KEY`, `QDRANT_URL`, `QDRANT_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` |
| Auth | `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES` |
| Files | `UPLOAD_DIR`, `MAX_UPLOAD_MB`, `SYNC_INGEST_MAX_CHUNKS`, `MAX_UNZIPPED_MB`, `MAX_ZIP_RATIO` |
| Extraction | `MIN_TEXT_CHARS_PER_PAGE`, `CAPTION_MIN_IMAGE_RATIO`, `SCANNED_PAGE_IMAGE_RATIO`, ... |
| Ingestion | `EMBEDDER` (`local` / `api`), `EMBEDDING_MODEL`, `SPARSE_MODEL`, `RERANKER_MODEL`, `CHUNK_SIZE`, `CHUNK_OVERLAP`, `QDRANT_COLLECTION`, `HNSW_M`, ... |
| Retrieval | `CANDIDATES` (30), `TOP_K` (6), `RERANK_CANDIDATES` (15), `REFUSAL_THRESHOLD` (0.03), `RRF_K`, `EXACT_TOKEN_BOOST` |
| LLM | `LLM_MODEL`, `SMALL_MODEL`, `LLM_TIMEOUT_S`, `LLM_MAX_RETRIES`, optional `PROMPT_CANARY` |
| API | `RATE_LIMIT`, `CORS_ORIGINS`, `LOG_LEVEL`, `HEALTH_TIMEOUT_S` |

Only the secrets group must be filled in; everything else has a sensible default in `app/config.py`.
Neon strings end in `?sslmode=require&channel_binding=require`, which asyncpg rejects; `app/db.py`
converts them automatically, so paste them as Neon gives them. Generate a JWT secret with
`uv run python -c "import secrets; print(secrets.token_urlsafe(48))"`.

### 3. Start Qdrant and create the tables

```powershell
docker compose up -d qdrant          # dashboard: http://localhost:6333/dashboard
uv run alembic upgrade head          # creates users and documents in Neon
```

### 4. Download the corpus (optional, for the demo and evaluation)

Download the PDFs listed in [`corpus/README.md`](corpus/README.md) into the same paths.

---

## Running

### API

```powershell
uv run fastapi dev app/main.py
```

- Swagger UI: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

### Frontend

```powershell
cd frontend
copy .env.example .env      # VITE_API_URL=http://127.0.0.1:8000
npm install
npm run dev                 # http://localhost:5173 (the API must be running)
```

`npm run build` type-checks and writes the static site to `frontend/dist/`. See
[`frontend/README.md`](frontend/README.md) for the frontend structure.

### Tests

```powershell
uv run pytest -v
```

327 tests: API tests for every endpoint (success and failure), guardrail and edge-case tests,
user-isolation and delete-then-never-retrieved tests, and unit tests for extraction, chunking,
retrieval, citation checking, metrics and the judge. Tests never load real models or call the real
LLM: they use `FakeEmbedder`, `FakeReranker`, `FakeLLM` and `FakeClassifier` through dependency
overrides, in-memory Qdrant, and the Neon `test` branch (`TEST_DATABASE_URL`, truncated before each
test).

Pipeline stages were first checked by eye on the real corpus in `notebooks/01_extract.ipynb` to
`05_answer.ipynb`, which are committed with their outputs.

### Lint and format

```powershell
uv run ruff check . --fix
uv run ruff format .
```

### Evaluation

```powershell
# Retrieval metrics only (free): hit rate@k, recall@k, MRR, latency
uv run python -m evals.run_eval --config final

# Compare every configuration
uv run python -m evals.run_eval --config all

# Full answer evaluation with the Opus judge (calls the Anthropic API)
uv run python -m evals.run_eval --config final --stage full --split heldout

# Urdu questions plus their English originals
uv run python -m evals.run_eval --config final --language ur --split all
```

Options: `--split tune|heldout|all`, `--language all|en|ur`, `--stage retrieval|full`, `--reingest`
(rebuild the collections after changing extraction or chunking code). Each config is ingested into
its own Qdrant collection, and the reports are saved as dated JSON + Markdown in `evals/results/`.

### Metrics report (Langfuse)

```powershell
uv run python scripts/metrics_report.py --last 50 --markdown
```

Prints p50/p95 latency per step, tokens, cost per question and error rate for the last traces
(`--tag api|eval|config:final`), and with `--markdown` saves the report to `evals/results/`.

---

## API reference

| Method | Path | Auth | Success | Errors |
| --- | --- | --- | --- | --- |
| POST | `/auth/signup` | public | 201 | 409 email taken, 422 |
| POST | `/auth/login` (OAuth2 password form) | public | 200 token | 401 |
| GET | `/auth/me` | token | 200 | 401 |
| POST | `/documents` (multipart file) | token | 201 ingested (202 processing for large files) | 400 empty / no text / corrupt, 401, 409 duplicate, 413 > 25 MB, 415 type, 503 |
| GET | `/documents` | token | 200 list | 401 |
| GET | `/documents/{doc_id}` | token, own only | 200 | 401, 404 |
| DELETE | `/documents/{doc_id}` | token, own only | 204 | 401, 404 |
| POST | `/ask` | token | 200 | 400 blocked by guardrail, 401, 422, 429, 503 |
| GET | `/health` | public | 200 / 503 | — |

Upload runs validation, extraction and chunking in the request. Files with more than
`SYNC_INGEST_MAX_CHUNKS` chunks (default 120) return **202** with status `processing` and are
embedded in a background task; the frontend polls `GET /documents/{doc_id}` until `ready`.

**Ask request**

```json
{"question": "What does error code 0052 mean?", "history": []}
```

**Ask response** (shortened)

```json
{
  "request_id": "…",
  "answer": "Error 0052 means … [1]",
  "answerable": true,
  "language": "en",
  "citations": [{"n": 1, "chunk_id": "…", "doc_id": "…", "filename": "di_api_technical_spec_v1.12.pdf",
                 "page_start": 0, "page_end": 0, "section_path": "…"}],
  "general_context": null,
  "chunk_count": 6,
  "retrieved_chunks": [{"n": 1, "chunk_id": "…", "filename": "…", "page_start": 0, "section_path": "…",
                        "text": "…", "similarity": 0.0, "rerank_score": 0.0}],
  "timings_ms": {"guardrails": 0, "retrieval": 0, "rerank": 0, "generation": 0, "total": 0}
}
```

**Error body** (every error, including 422 and 500):

```json
{"error": {"code": "document_not_found", "message": "…", "request_id": "…"}}
```

Full schemas and examples (including an Urdu `/ask` example) are in Swagger at `/docs`.

---

## Evaluation results

Golden set: [`evals/golden_set.jsonl`](evals/golden_set.jsonl), 50 English questions (30 `tune`,
20 `heldout`) across exact codes, field rules, legal rules, reworded FAQs, tables/matrices,
multi-part, screenshot-only, version-sensitive and unanswerable questions, plus 8 Urdu
translations. Each question is labelled with document + page range (not chunk ids), so different
chunking strategies are compared fairly.

### Retrieval: configuration comparison (tune split, k = 5)

| Config | What changes | hit@5 | recall@5 | MRR |
| --- | --- | --- | --- | --- |
| `baseline` | Fixed 512-token chunks, dense only | 0.720 | 0.660 | 0.451 |
| `structure_dense` | Structure-aware chunks, dense only | 0.800 | 0.720 | 0.640 |
| `structure_hybrid` | + BM25 sparse, RRF fusion | 0.800 | 0.800 | 0.640 |
| **`final`** | + reranker + exact-token and authority boosts | **0.920** | **0.880** | **0.753** |

Source: [`evals/results/2026-11-20_retrieval_comparison.md`](evals/results/2026-11-20_retrieval_comparison.md).
The reranker is the main latency cost (CPU cross-encoder), traded for +12 points of hit rate.

### Answers: `final` config (tune split, 30 questions, judge `claude-opus-5-5`)

| Metric | Value |
| --- | --- |
| Faithfulness (answered) | 0.909 |
| Answer correctness (factual F1) | 0.658 |
| Judge: correct / partly / wrong | 24 / 4 / 2 |
| Citation support | 1.000 |
| Unanswerable questions correctly refused | 100% |
| False refusals | 4% |

Source: [`evals/results/2026-11-20_final_full.md`](evals/results/2026-11-20_final_full.md), which
also contains the refusal-threshold sweep and the per-question failure list.

### Urdu (bonus)

| Asked in | n | hit@5 | recall@5 | MRR |
| --- | --- | --- | --- | --- |
| English original | 8 | 1.000 | 0.929 | 0.857 |
| Urdu translation | 8 | 1.000 | 0.929 | 0.786 |

Source: [`evals/results/2026-11-22_final_ur.md`](evals/results/2026-11-22_final_ur.md).

---

## Deployment

**Status: pending.** The planned production setup is one Linux VPS (4 vCPU / 8 GB RAM) running
Docker Compose with three services: `api` (FastAPI + models), `qdrant` (with a persistent volume) and
`caddy` (serves the built frontend and reverse-proxies the API with automatic HTTPS). Neon
PostgreSQL and Langfuse stay as managed cloud services.

Live URL: *to be added after deployment.*

---

## Development process

- Built in phases, one feature branch per phase (`feature/project-setup`, `feature/auth`,
  `feature/evals-golden-set`, `feature/ingestion`, `feature/retrieval`, `feature/ask-endpoint`,
  `feature/guardrails`, `feature/observability`, `feature/frontend`, `feature/multilingual`), each
  merged into `main` through a pull request with Conventional Commits.
- The phase plan is in [`docs/DEVELOPMENT_PLAN.md`](docs/DEVELOPMENT_PLAN.md).
- Built with Claude Code as a pair programmer: exported sessions are in
  [`docs/ai-logs/`](docs/ai-logs/), and the developer's own explanations of each phase are in
  [`docs/learning-logs/`](docs/learning-logs/).

---

## Security notes

- `.env` is git-ignored and never committed; `.env.example` contains names only.
- Passwords are hashed with argon2; tokens, passwords, keys and full document text are never logged.
- Uploaded files are stored per user under `UPLOAD_DIR/<owner_id>/` and deleted with the document.
- Text inside documents is treated as data, never as instructions; the system prompt carries a
  random canary, and any answer that leaks it (or an API key / JWT-like string) is replaced with a
  refusal.

---

## Author

Abid — IEC Cohort 35, Portfolio 1.
