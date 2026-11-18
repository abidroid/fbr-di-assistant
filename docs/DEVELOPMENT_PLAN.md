# FBR Digital Invoicing Assistant — Development Plan

Portfolio 1 · Production RAG Service · Nov 15, 2026 · @Muhammad Abid · Deadline **Wed 25 Nov 2026**

This file is for you; `CLAUDE.md` is for Claude Code. Feed Claude Code one phase at a time using the
prompt in each phase. Every phase ends with you explaining the new code back, so by demo day you can
defend every line. The full design reasoning (why each tool, which alternatives were rejected) is in
the Assignment 5 plan, and gets copied into `DESIGN.md` in Phase 12.

## 1. Goal and scoring strategy

Build every mandatory item first, then all eight bonuses. Bonus marks (28) can recover lost mandatory
marks up to the 100 cap. **M7 is the gate: no measured evaluation = F**, so evaluation starts on day 2,
not at the end.

| ID | Criterion | Marks | What earns full marks | Phase |
| --- | --- | --- | --- | --- |
| M7 | Golden set & measured results | 12 | ≥ 25 questions (we do 50) with source labels and reference answers, ≥ 5 unanswerable, reworded and multi-part; hit rate@k, recall@k, MRR; faithfulness, correctness, unanswerable handling | 3, 5, 6 |
| M3 | DESIGN.md justification | 9 | Every decision: choice, why, rejected alternatives, trade-off in quality/cost/speed/latency, backed by eval numbers | 12 |
| M2 | Frontend | 8 | Upload, document list, ask, answers with citations, chunks panel with count and similarity scores | 9 |
| M5 | Ingestion | 8 | PDF + DOCX; metadata (doc id, file name, page/section, position); duplicate detection; delete removes vectors | 4 |
| M6 | Grounded answers | 8 | Every citation points to a retrieved chunk; clear "not found"; no invented facts | 6 |
| M8 | Eval frameworks & reproducibility | 8 | RAGAS + LLM judge + Langfuse justified; judge reliability checked; ≥ 2 configs compared; one command; failure analysis | 5, 6, 8 |
| M15 | Demo video | 8 | All 14 checklist items, explained in your own words | 12 |
| M1 | Project structure | 6 | Layered; routes only call services; one config module | 1 |
| M9 | Guardrails | 6 | Empty/long, off-topic, injection, jailbreak; no invented citations; no prompt/key leaks; each proven on video | 7 |
| M11 | Testing | 6 | Notebooks first, then API tests; success and failure cases; executed | 4–7 |
| M12 | Code quality | 6 | Docstrings, types, config module, timeouts + retries, async, parallel, request logs | all |
| M4 | API design | 4 | Clean routes for upload, list, delete, ask; explained in DESIGN.md | 2, 4, 6 |
| M10 | Error handling | 4 | Correct 200/201/204/400/404/409/415/422/429/503, one error shape | 1, 7 |
| M13 | Git & GitHub | 4 | Feature branches, many meaningful commits, no leaked keys | all |
| M14 | Submission docs | 3 | README, pinned requirements, .env.example, Swagger screenshots, AI usage docs | 12 |
| B1 | JWT auth | 5 | Hashed passwords, token required, per-user isolation | 2 |
| B2 | Multilingual | 4 | Urdu questions, answers in Urdu, reported separately | 10 |
| B3 | Monitoring | 4 | Traces per step; p50/p95, tokens, cost/question, error rate on a dashboard | 8 |
| B7 | Edge cases | 4 | 8 or more handled and shown | 7 |
| B4 | Modern frontend | 3 | React + Tailwind + shadcn/ui | 9 |
| B6 | Document guardrails | 3 | Hidden instructions ignored; corrupt/malicious files rejected | 4, 7 |
| B8 | Deployment | 3 | Backend + frontend on a public URL in README | 11 |
| B5 | Extra file types | 2 | TXT, MD, PPTX | 4 |

Rubric traps: (1) a feature not shown in the video loses marks in its own criterion; (2) the AI usage
section needs the complete prompts and chat history, so export every Claude Code session; (3) a leaked
API key in Git costs marks, so `.env` goes in `.gitignore` before the first commit.

## 2. How to run each phase (the learning loop)

1. `git switch main; git pull; git switch -c <branch>`.
2. Start `claude`, switch to plan mode (Shift+Tab), paste the phase prompt.
3. Read Claude's concept briefing and plan. Ask about anything unclear **before** approving.
4. Let it build. Watch the files it creates; ask "why" whenever something is new.
5. Run the "Done when" checks yourself.
6. Answer the **Explain it back** questions in your own words. Claude corrects you and saves your
   answers to `docs/LEARNING_LOG.md`. These become your video notes.
7. `/export docs/ai-logs/phase-NN.md`, then ruff + pytest, push, `gh pr create --fill`, merge.

## 3. Architecture in one picture

```text
UPLOAD  React -> POST /documents -> guardrails.validate -> ingestion.extract -> ingestion.chunk
        -> ingestion.embed (BGE-M3 dense + BM25 sparse) -> ingestion.store (Qdrant) ; record in Neon

ASK     React -> POST /ask -> guardrails.check_input -> (rewrite follow-up, Haiku)
        -> retrieval: dense top30 + bm25 top30 -> RRF -> rerank (bge-reranker-v2-m3) -> top 6
        -> below threshold? -> "I couldn't find this in your documents."
        -> generation (Sonnet, JSON) -> citation check + leak check -> AskResponse (+ chunks panel)
        every step traced in Langfuse; one log line per request
```

## 4. The golden set format (decide this before any code)

Labels must not depend on how you chunk, otherwise comparing two chunking strategies is unfair.
So each question is labelled with **document + page range (+ section)**, not chunk ids. A retrieved
chunk is a hit when it comes from the same document and its page range overlaps the label.

```json
{"id": "q017", "question": "How long can the Commissioner extend the date for integration?",
 "category": "legal_rule", "answerable": true, "language": "en",
 "sources": [{"doc": "sales_tax_rules_2006_upto_2026-07-31.pdf", "pages": [91], "section": "Rule 150V"}],
 "reference_answer": "Up to sixty days in aggregate, in intervals of fifteen days (Rule 150V); paper invoices continue meanwhile.",
 "split": "tune"}
```

Categories and counts (50 total, 30 `tune` / 20 `heldout`, plus 8 Urdu in Phase 10):
exact code 7 · field rules 7 · legal rule 7 · reworded FAQ→law 5 · tables/matrix 5 ·
multi-part/multi-doc 5 · screenshot-only 3 · version-sensitive 3 · unanswerable 8.

## 5. Evaluation configurations

One YAML per config in `evals/configs/`, each ingested into its own Qdrant collection.

| Config | Chunking | Retrieval | Purpose |
| --- | --- | --- | --- |
| `baseline` | fixed_512, overlap 50 | dense only | The "before" number |
| `structure_dense` | structure-aware | dense only | Does chunking help? |
| `structure_hybrid` | structure-aware | dense + BM25, RRF | Do exact codes need keywords? |
| `final` | structure-aware | hybrid + reranker + boosts | The chosen system |
| `final_textonly` (optional) | structure-aware, no OCR/captions | as final | Value of multimodal ingestion |

The brief needs at least two; we report four so every big decision has a number behind it.

## 6. Phases

### Phase 0 — Environment (manual, before Claude Code) · Sun 15 Nov

Accounts and keys (keep all values off camera):
- **Neon:** project `fbr-di-assistant`, region Singapore; keep the default `production` branch, create
  branch `test`. Copy both DIRECT connection strings.
- **Anthropic:** API key with a small spending limit.
- **Langfuse Cloud:** free project; copy public key, secret key and host.
- **Docker Desktop** installed and running. **Node.js LTS** (`node -v`). **GitHub CLI** logged in.

Create the project folder, copy in the corpus you already collected, then scaffold:

```powershell
cd D:\development\iec35
mkdir fbr-di-assistant
cd fbr-di-assistant
mkdir corpus\legal, corpus\technical, corpus\portal, scripts, docs\ai-logs, docs\screenshots
$old = "D:\Learning\iec\iec-35\Portfolio1\fbr-di-assistant"
Copy-Item "$old\legal\*" corpus\legal\
Copy-Item "$old\faqs.jsonl" corpus\
Copy-Item "$old\*.py" scripts\
# copy the DI API spec PDF into corpus\technical\ and the IRIS manual into corpus\portal\
uv init --python 3.12 --name fbr-di-assistant .
uv add "fastapi[standard]" "sqlalchemy[asyncio]" asyncpg alembic pydantic-settings pyjwt "pwdlib[argon2]" anthropic qdrant-client tenacity
uv add --dev pytest pytest-asyncio httpx ruff jupyter ipykernel
git init -b main
```

Create `.gitignore` FIRST (`.env`, `.venv/`, `corpus/**/*.pdf`, `uploads/`, `qdrant_storage/`,
`__pycache__/`, `.ipynb_checkpoints/`, `node_modules/`, `frontend/dist/`), plus `.gitattributes`
with `* text=auto`. Copy in `CLAUDE.md` and this file as `docs/DEVELOPMENT_PLAN.md`. Then:

```powershell
git add .; git commit -m "chore: initialise project with uv, CLAUDE.md and plan"
gh repo create fbr-di-assistant --public --source . --remote origin --push
```

Done when `uv run python -c "import fastapi, qdrant_client, anthropic"` works and the repo is public
without any PDF or `.env` in it (check on GitHub).

**Understand it:** why `uv.lock` + pinned `requirements.txt` make the project reproducible; why the
corpus PDFs stay out of Git (the DI spec says it should not be redistributed; README links the sources).

### Phase 1 — `feature/project-setup` · Sun 15 Nov

```text
Read CLAUDE.md fully. Create branch feature/project-setup. Build app/config.py (Settings with every
setting listed in CLAUDE.md, including DATABASE_URL, TEST_DATABASE_URL, JWT_SECRET_KEY, ANTHROPIC_API_KEY,
QDRANT_URL, LANGFUSE_*, UPLOAD_DIR, MAX_UPLOAD_MB=25, TOP_K=6, CANDIDATES=30, REFUSAL_THRESHOLD=0.30,
RATE_LIMIT="20/minute", LLM_MODEL, SMALL_MODEL, CORS_ORIGINS, PROMPT_CANARY). Build app/db.py with the
Neon fixes from CLAUDE.md, app/models.py with User and Document exactly as the data model, Alembic
(async template) wired to Settings and Base, and the first migration. Build app/exceptions.py,
app/observability.py (logging config + request-id middleware that logs method, path, status,
latency_ms), and app/main.py with create_app(), one exception handler producing the
{"error": {code, message, request_id}} shape for domain errors, RequestValidationError (422) and
unhandled errors (500), CORS, and GET /health that checks Neon. Add docker-compose.yml with a qdrant
service (port 6333, named volume) and extend /health to ping Qdrant. Add .env.example with every key
and dummy values. Docstrings everywhere. Teaching mode: brief me first, show the plan, then commit in
small steps.
```

Done when `docker compose up -d qdrant` runs, `uv run alembic upgrade head` creates both tables in
Neon, `uv run fastapi dev app/main.py` starts, and `/health` returns 200 with Neon and Qdrant "ok".

**Understand it:** app factory and lifespan; why settings live in one module; what a migration is;
why Neon needs the asyncpg URL fix; what a request id is for.

**Explain it back:** (1) What happens, in order, when a request hits the API? (2) Why does the error
handler live in `main.py` and not in the services? (3) What would break if `.env` were committed?

### Phase 2 — `feature/auth` · Mon 16 Nov (morning)

```text
Create branch feature/auth. Add app/security.py (pwdlib argon2, PyJWT HS256), app/services/auth_service.py,
app/routes/auth.py with POST /auth/signup, POST /auth/login (OAuth2PasswordRequestForm) and GET /auth/me,
and a CurrentUser dependency in app/deps.py returning 401 with WWW-Authenticate: Bearer. Create
tests/conftest.py per the Testing section of CLAUDE.md (Neon test branch, NullPool, truncate per test,
httpx AsyncClient, get_db override) and tests/test_auth.py with success and failure cases (duplicate
email 409, wrong password 401, missing/bad/expired token 401, invalid email 422). Add swagger examples.
Run pytest until green.
```

Done when the Swagger Authorize button logs you in and all auth tests pass.

**Understand it:** hashing vs encryption; what a JWT contains and why it is signed, not encrypted;
why another user's document must return 404, not 403.

**Explain it back:** (1) Why argon2 instead of storing passwords? (2) What does the server check
when a token arrives? (3) Why do tests run on a separate Neon branch?

### Phase 3 — `feature/evals-golden-set` · Mon 16 Nov (afternoon)

This phase is half yours: Claude drafts, **you verify every answer against the PDF**. A wrong
reference answer silently poisons every later number.

```text
Create branch feature/evals-golden-set. (1) Write evals/metrics.py with pure functions hit_rate_at_k,
recall_at_k and mrr that take retrieved chunk metadata and the question's source labels (doc + page
overlap, as in section 4 of docs/DEVELOPMENT_PLAN.md), with unit tests in tests/unit/test_metrics.py
including edge cases (no sources, unanswerable questions, duplicates). (2) Read the corpus files in
corpus/ (use PyMuPDF to read pages) and draft evals/golden_set.jsonl: 50 questions in the categories
and counts from section 4, phrased the way a developer would ask (never copy corpus sentences; reword
FAQ questions), each with sources (doc, pages, section) and a one- or two-sentence reference answer,
and a 30 tune / 20 heldout split balanced across categories. Include 8 unanswerable questions that
are close to the domain but not covered (e.g. income tax rates). (3) Write evals/README.md explaining
the format. Show me the questions in batches of 10 so I can check each against the PDF before saving.
```

Done when metrics tests pass and you have personally checked all 50 answers and page numbers.

**Understand it:** hit rate@k vs recall@k vs MRR (with a worked example); why labels use pages, not
chunk ids; why a held-out split prevents fooling yourself.

**Explain it back:** (1) A question has 2 correct sources and only 1 is in the top 5: what are hit
rate@5 and recall@5? (2) The first correct chunk is at rank 3: what is the reciprocal rank? (3) Why
must the held-out 20 not be used while tuning?

### Phase 4 — `feature/ingestion` · Tue 17 Nov

```text
Create branch feature/ingestion. Build app/ingestion/ per CLAUDE.md:
validate.py (25 MB limit -> 413, magic-byte type check -> 415, encrypted/corrupt PDF and zip-bomb
DOCX/PPTX -> 400, empty file -> 400); extract.py returning ExtractedBlock(text, page, section_path,
kind) for PDF (PyMuPDF for pages with a text layer, Docling for layout/tables and OCR on pages without
text), DOCX and PPTX (Docling), TXT and MD (split on headings); image-only pages captioned with the
small Claude model; chunk.py with two strategies, fixed_512 (baseline) and structure (legal
section/rule, error-code rows, matrix rows with headers repeated, whole JSON samples, FAQ Q&A,
captions, recursive ~500/75 fallback), stripping repeated page headers/footers and moving amendment
footnotes into metadata, prepending the context header; embed.py with an Embedder protocol,
BGEM3Embedder (dense, normalised), Bm25Embedder (fastembed Qdrant/bm25) and FakeEmbedder for tests,
batched and run in a thread; store.py creating the collection (named vectors dense + bm25 with IDF,
HNSW m=16 ef_construct=100, payload indexes), upserting chunks with the full payload from CLAUDE.md,
and deleting by doc_id. app/services/document_service.py orchestrates: sha256 duplicate check (409
with existing id), save file, extract, chunk, embed, store, update the Document row (pages,
chunk_count, ocr_pages, warnings, status). Routes POST/GET /documents, GET/DELETE /documents/{id}
(own documents only; delete removes vectors, file and row). Flag chunks with instruction-like text
as suspicious=true. Load models once in the lifespan. Create notebooks/01_extract, 02_chunk and
03_embed running each stage on real corpus files with visible output. Tests with FakeEmbedder and
in-memory Qdrant: upload PDF and DOCX 201, list, get, delete 204 then zero vectors left for that
doc, duplicate 409, unsupported type 415, empty file 400, too large 413, other user's doc 404.
Add uv dependencies docling, pymupdf, python-docx, python-pptx, FlagEmbedding, fastembed,
langchain-text-splitters, python-multipart.
```

Done when uploading the Sales Tax Rules PDF in Swagger returns 201 with a sensible chunk count, the
Qdrant dashboard shows points with the full payload, and deleting it leaves no points for that doc.

**Understand it:** why structure-aware chunks beat fixed windows for laws and tables; dense vs sparse
vectors; what HNSW does; why models load once in the lifespan; why CPU work runs in a thread.

**Explain it back:** (1) Walk a PDF from upload to stored points. (2) Why prepend a context header
before embedding? (3) How do you prove deleted content can never be retrieved?

### Phase 5 — `feature/retrieval` · Wed 18 Nov

```text
Create branch feature/retrieval. Build app/retrieval.py: embed the query (dense + bm25 concurrently),
Qdrant query with two prefetches (dense top CANDIDATES, bm25 top CANDIDATES) fused by RRF, filtered by
owner_id and superseded=false; exact-token boost (regex for error codes like 0052, scenario ids like
SN009, rule/section numbers like 150XF or s.33, camelCase JSON field names); rerank with
bge-reranker-v2-m3 (FlagReranker, normalised scores, in a thread); authority boost; return top_k
chunks with cosine similarity and rerank score; report whether the best score is under
REFUSAL_THRESHOLD. Make every step switchable from an eval config. Build evals/run_eval.py: given
--config, ingest the whole corpus into that config's collection (skip if already ingested at the same
config hash), run every golden question through retrieval only (--stage retrieval) and save hit
rate@5, recall@5, MRR overall and per category to evals/results/<date>_<config>.json and .md.
Create the four configs from section 5 of the plan. Create notebooks/04_retrieve comparing dense,
bm25, hybrid and reranked results for 10 sample questions. Unit-test the boost regex and RRF wiring.
Run all four configs on the tune split and show me the table.
```

Done when you have a 4-row retrieval table with real numbers and can say which change helped most.

**Understand it:** why BM25 finds `0052` when embeddings miss it; how RRF combines rankings without
comparing scores; why a cross-encoder reranker is more accurate but slower; how the threshold trades
false refusals against hallucinations.

**Explain it back:** (1) Why do we fetch 30 candidates but keep only 6? (2) What does RRF actually add
up? (3) Read your table: which decision earned the biggest gain, and why do you think so?

### Phase 6 — `feature/ask-endpoint` · Thu 19 Nov

```text
Create branch feature/ask-endpoint. Build app/generation.py per the Generation rules in CLAUDE.md:
follow-up rewrite with the small model only when history exists; prompt with numbered <document>
blocks; AsyncAnthropic call with timeout and retries; JSON parsed into a Pydantic model (retry once);
citation check that drops unknown [n] and regenerates once if nothing valid remains; refusal when
retrieval is under threshold (no LLM call) or the model says not answerable; leak check with the
canary. POST /ask returns AskResponse exactly as in CLAUDE.md, with timings. Extend run_eval.py with
--stage full: answers for every question, then evals/judge.py computing RAGAS faithfulness and answer
correctness, an LLM-as-judge verdict (correct / partly / wrong) plus citation support, and
unanswerable accuracy (refused when it should, false refusals when it should not). Add
evals/judge_check.py that shows me 20 answers to label by hand and reports judge agreement. Create
notebooks/05_answer. Tests with FakeLLM: answer with valid citations, invented citation removed,
refusal path, empty-index message, leak replaced.
```

Done when `/ask` answers "How long can the Commissioner extend the integration date?" with a
citation to Rule 150V, refuses "What is the income tax rate for salaried people?", and the full eval
writes correctness and faithfulness numbers.

**Understand it:** why citations are built by code from metadata, not by the model; faithfulness vs
correctness; why an LLM judge must be checked against human labels.

**Explain it back:** (1) How can the model never invent a citation? (2) An answer is faithful but
wrong: how is that possible? (3) What was your judge agreement, and what would you do if it were 60%?

### Phase 7 — `feature/guardrails` · Fri 20 Nov

```text
Create branch feature/guardrails. Build app/guardrails.py: input checks (empty -> 422, > 1000 chars
-> 422, injection and jailbreak patterns then a small-model classifier returning ok | injection |
jailbreak | off_topic -> 400 with a fixed polite message for injection/jailbreak, polite refusal for
off_topic), rate limit with slowapi using RATE_LIMIT per user -> 429; output checks already in
generation are moved here and reused. Make every status code in the brief's table reachable and
consistent, including 503 when Anthropic, Qdrant or Neon fail after retries. Handle and test the edge
cases: scanned PDF (OCR warning), no-text PDF 400, duplicate 409, large file (413, and 202 background
path above SYNC_INGEST_MAX_PAGES), question before any upload, unanswerable question, question about
a deleted document, contradicting documents (cite both, newer as current), Urdu and special
characters, API timeout -> 503, concurrent uploads of the same file (per-hash lock). Create
tests/fixtures/ with a planted-instruction PDF, a corrupt PDF, a renamed .exe and a zip bomb, and
tests/test_guardrails.py covering every guardrail and edge case.
```

Done when each guardrail and edge case has a passing test and you can trigger each one in Swagger.

**Understand it:** prompt injection vs jailbreak; why rule checks run before the classifier (cost,
speed); why documents are wrapped as data; why 404 hides other users' documents.

**Explain it back:** (1) Show an injection attempt and explain each layer that stops it. (2) What
happens, step by step, when Anthropic times out? (3) Why is off-topic a 200 refusal but injection a 400?

### Phase 8 — `feature/observability` · Fri 20 Nov (afternoon)

```text
Create branch feature/observability. Add Langfuse tracing (Python SDK, @observe or explicit spans):
one trace per request with spans for guardrails, query embedding, search, rerank and generation;
record model, input/output tokens and cost on the generation span; tag traces with request_id and a
hashed user id (never the email). Tag evaluation runs with the config name. Add a scripts/metrics_report.py
that pulls the last N traces and prints p50/p95 latency per step, tokens, cost per question and error
rate. Make tracing a no-op when LANGFUSE keys are absent (tests must not need them).
```

Done when one question in the UI produces a trace in Langfuse with every step and its cost.

**Explain it back:** (1) Which step is slowest, and why? (2) What does one question cost?

### Phase 9 — `feature/frontend` · Sat 21 Nov

```text
Read the Frontend row in CLAUDE.md. Create branch feature/frontend. Scaffold React + Vite + TypeScript
in frontend/, add Tailwind v4 (@tailwindcss/vite), shadcn/ui (button, card, input, label, table,
dialog, alert-dialog, badge, collapsible, scroll-area, sonner, progress, tabs), TanStack Query, React
Router, react-hook-form + zod. src/lib/api.ts typed from /openapi.json, base URL from VITE_API_URL;
AuthContext with the JWT in localStorage, Bearer header, logout on 401. Pages: Login, Signup, and a
main page with (a) Documents: drag-and-drop upload (shows 201/409/413/415 messages as toasts),
list with status badge, pages, chunks, OCR warning, delete with confirm; (b) Chat: question box,
answers with inline [n] citations linking to sources, "General context" shown separately, an
"I couldn't find this" style for refusals, and under each answer a collapsible "Retrieved chunks
(N)" panel listing every chunk's text, file, page, section, cosine similarity and rerank score.
Loading and empty states everywhere; clean, neutral theme, one accent colour. npm run build must pass.
```

Done when the full flow works in the browser and the chunks panel shows count and both scores.

**Explain it back:** (1) How does the frontend know who the user is? (2) Where do the scores in the
chunks panel come from?

### Phase 10 — `feature/multilingual` · Sat 21 Nov (evening)

```text
Create branch feature/multilingual. Add 8 Urdu questions to the golden set (translations of 8
existing English questions across categories, language "ur"), make sure answers come back in the
question's language, and report Urdu results separately in run_eval output. Add an Urdu example to
the Swagger docs and a test that an Urdu question gets an Urdu answer (FakeLLM checks the instruction).
```

**Explain it back:** why can BGE-M3 match an Urdu question to an English chunk without translation?

### Phase 11 — `feature/deploy` · Mon 23 Nov

```text
Create branch feature/deploy. Add a production Dockerfile for the API (python 3.12-slim, uv sync
--frozen, model weights downloaded at build or first start into a volume), a frontend build stage, a
Caddyfile serving the built frontend and reverse-proxying /api to the API with automatic HTTPS, and a
docker-compose.prod.yml (api, qdrant with a volume, caddy). Add scripts/seed_demo.py that creates the
demo user and uploads the corpus. Write the deployment steps for a 4 vCPU / 8 GB Ubuntu VPS in
README (Docker install, .env on the server, compose up, alembic upgrade). Do not put secrets in any file.
```

Then on the server: deploy, seed, and run `run_eval --config final --split heldout` **against the
live service**. Those held-out numbers are the ones in EVAL.md.

**Explain it back:** (1) What runs where in production? (2) Why are the held-out numbers measured on
the live server?

### Phase 12 — `docs/submission` · Mon 23 – Tue 24 Nov

```text
Create branch docs/submission. Write README.md (overview, corpus and how to download it, architecture
diagram, tech stack, setup with uv, Neon and Qdrant setup, .env, run API/frontend/tests/eval, live
URL, screenshots). Write DESIGN.md: architecture plus every decision from the brief's table (corpus,
text extraction, chunking, embedding, vector DB, retrieval settings, LLM, prompt design) and also auth,
guardrails, API design and testing approach, each with choice, why, rejected alternatives and the
quality/cost/speed/latency trade-off, citing numbers from evals/results. Write EVAL.md: golden set
summary, frameworks and why, judge reliability, the configuration comparison table with the winner,
held-out results, Urdu results, latency and cost, and a failure analysis of the wrong answers by
cause. Regenerate requirements.txt. Write docs/AI_USAGE.md (tools, models, settings, list of prompts,
links to docs/ai-logs/ and the claude.ai planning chat).
```

Take Swagger screenshots (one per endpoint, success and key errors) into `docs/screenshots/`.

## 7. Testing strategy

| Level | Tool | What | Phase |
| --- | --- | --- | --- |
| Pipeline, by eye | Jupyter notebooks 01–05 | Real corpus through each stage, outputs saved | 4–6 |
| Unit | pytest | metrics, chunkers, boost regex, citation checker, guardrail rules | 3–7 |
| API | pytest + httpx, fakes for models/LLM, in-memory Qdrant, Neon test branch | every endpoint, success and failure | 2, 4, 6, 7 |
| Behaviour | pytest | delete then never retrieved; user isolation; injection/jailbreak blocked | 4, 7 |
| Live smoke | `pytest -m live` (real LLM, 5 questions) | before the demo | 11 |
| Quality | `evals/run_eval.py` | retrieval and answer metrics | 5, 6, 11 |

## 8. Schedule

| Day | Phases | Merged by end of day |
| --- | --- | --- |
| Sun 15 Nov | 0, 1 | `feature/project-setup` |
| Mon 16 Nov | 2, 3 | `feature/auth`, `feature/evals-golden-set` |
| Tue 17 Nov | 4 | `feature/ingestion` |
| Wed 18 Nov | 5 | `feature/retrieval` |
| Thu 19 Nov | 6 | `feature/ask-endpoint` |
| Fri 20 Nov | 7, 8 | `feature/guardrails`, `feature/observability` |
| Sat 21 Nov | 9, 10 | `feature/frontend`, `feature/multilingual` |
| Sun 22 Nov | Catch-up, full eval on all configs, fix worst failures | — |
| Mon 23 Nov | 11, start 12 | `feature/deploy` |
| Tue 24 Nov | 12, screenshots, record video | `docs/submission` |
| Wed 25 Nov | Fresh-clone test, tag `v1.0.0`, submit | — |

If you fall behind, cut in this order: Phase 10, then Phase 8, then the optional eval config. Never cut
Phase 3, 5 or 6.

## 9. Demo video script (brief checklist)

| # | Segment | Min | Show |
| --- | --- | --- | --- |
| 1 | Introduction | 1 | Corpus (which documents, how many, file types), what the service does |
| 2 | Architecture | 2 | Project tree; upload flow and ask flow |
| 3 | Design decisions | 3 | Chunking, embeddings, Qdrant, LLM, each with its eval number |
| 4 | Upload | 1 | PDF and DOCX through the UI; list updates |
| 5 | Questions | 2 | 3+ cited answers; chunks panel with count and scores |
| 6 | Unanswerable | 0.5 | Clear refusal |
| 7 | Delete | 1 | Delete a document; ask again; content gone |
| 8 | Guardrails | 1.5 | Empty, too long, off-topic, injection, jailbreak, planted instruction in a file |
| 9 | Errors | 1 | 415 unsupported type, 409 duplicate, 413 too large |
| 10 | Edge cases | 1.5 | Each handled case |
| 11 | Evaluation | 2 | Run the command; walk through EVAL.md comparison and failure analysis |
| 12 | Tests | 0.5 | `pytest -v` green |
| 13 | API docs | 0.5 | Swagger |
| 14 | Bonuses | 1.5 | JWT isolation, Urdu, Langfuse dashboard, live URL |

Segments 3 and 11 decide whether the panel believes you understand the system. Rehearse them from
your `LEARNING_LOG.md`, in your own words.

## 10. Final checklist

- [ ] Fresh clone + README only: install, configure, run API, frontend, tests and eval
- [ ] `uv run pytest -v` green; summary line in README
- [ ] `uv run ruff check .` clean; docstrings everywhere
- [ ] No business logic in routes; settings only in `config.py`; no secrets in Git history
- [ ] `requirements.txt` pinned and current; `.env.example` complete
- [ ] EVAL.md numbers match the latest `evals/results/` files
- [ ] Notebooks committed with outputs
- [ ] Swagger screenshots in `docs/screenshots/`
- [ ] `docs/AI_USAGE.md` with prompts, all `docs/ai-logs/`, tools, models, settings
- [ ] Live URL works and is in README
- [ ] Demo video covers all 14 segments; link in README
- [ ] Repo public; every feature branch merged through a PR; tag `v1.0.0`
