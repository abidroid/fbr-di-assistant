# Learning Log: Phase 9 (Frontend)

Concepts: a single-page app (SPA) talking to the API only over HTTP/JSON, Vite (dev server and
static build), CORS (the browser blocks 5173 -> 8000 unless the API allows the origin), a JWT kept
in localStorage and sent as `Authorization: Bearer`, TanStack Query (`useQuery` caches reads,
`useMutation` does writes, invalidation refreshes lists), polling with `refetchInterval` while a
202 upload is still processing, shadcn/ui (components copied into our repo, styled with Tailwind
v4), react-hook-form + zod mirroring the backend's Pydantic rules.

Checked in the browser on 2026-11-22 (Chrome, local API + Qdrant + Neon): zod errors on an empty
signup; the backend's 422 message shown for a reserved `.test` email; signup logs straight in;
upload of `extension_for_integration_2025.pdf` -> 201, Ready, 1 page, 1 chunk; same file again ->
409; text renamed to `.pdf` -> 415 "file content does not match its .pdf extension"; a question the
notice cannot answer -> refusal style with "Retrieved chunks (1)" showing cosine 0.627 and rerank
0.197 (below the 0.30 threshold); an answerable question -> answer with two `[1]` citations, a
Sources list and the legal disclaimer (11.4 s total, rerank 5.8 s); clicking `[1]` highlights the
source; a prompt-injection question -> "Blocked by a guardrail" bubble; delete with confirm -> list
empty; logout -> back to /login with the token cleared.

**1. How does the frontend know who the user is?**

My words: _(to add before the video)_

Fuller answer (from review): It does not decide it; the backend does, from the token. At login
`api.login` sends the email and password as an OAuth2 form to `POST /auth/login` and gets back a
signed JWT. `AuthContext` stores it in localStorage (so a reload keeps you logged in) and
`request()` in `api.ts` adds `Authorization: Bearer <token>` to every call. The user's name in the
header comes from `GET /auth/me`, which the backend answers by verifying the token's signature and
reading the user id inside it. `RequireAuth` only hides pages from someone with no token; the real
protection is that every document and ask endpoint returns 401 without a valid token, and returns
only that user's documents with one. When the token expires the API answers 401, `request()` calls
the registered handler, and `AuthContext` clears the token and the query cache and redirects to
/login. Trade-off: localStorage is simple and survives reloads, but any script on the page could
read it (XSS); an httpOnly cookie is safer but needs CSRF protection and cookie setup on the API,
which is more than this project needs.

**2. Where do the scores in the chunks panel come from?**

My words: _(to add before the video)_

Fuller answer (from review): Both come from the backend, in `AskResponse.retrieved_chunks`; the
frontend only displays them. **Cosine similarity** compares the question's BGE-M3 dense embedding
with the chunk's stored embedding (the angle between two 1024-number vectors; 1 = same direction).
It is cheap because chunks were embedded once at upload, so it is used to *find* candidates.
**Rerank score** comes from `bge-reranker-v2-m3`, a cross-encoder that reads the question and the
chunk *together* and outputs 0-1 relevance; it is slower (one model pass per pair) but more
accurate, so it decides the final order and the refusal threshold (0.30). That is why a chunk can
have a decent cosine (0.627) and still a low rerank score (0.197): the words and topic are close,
but the passage does not actually answer the question, so the system refused. "Retrieved chunks
(N)" is how many chunks were sent to the model; a "cited" badge marks the ones the answer used.

**3. Why does the upload return either 201 or 202, and how does the UI handle each?**

My words: _(to add before the video)_

Fuller answer (from review): Embedding costs about 1 s per chunk on CPU, so a big file would keep
the request open for minutes. Files up to `SYNC_INGEST_MAX_CHUNKS` (120) are fully ingested in the
request and return **201** with status `ready`; larger ones return **202** with status
`processing` and finish in a background task. `uploadDocument` returns the HTTP status so the
dropzone can show "is ready" or "is being processed". The documents query has a `refetchInterval`
that polls every 3 s while any document is queued or processing and stops once all are ready or
failed, so the badge updates by itself without a page reload.
