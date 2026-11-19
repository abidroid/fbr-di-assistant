# Learning Log: Phase 1 (Project setup)

Concepts: settings module, app factory and lifespan, async engine and session, Alembic migrations,
domain exceptions with one error handler, request-id middleware, health check.

> **Note:** the answers below are model answers written by Claude Code, because I was not yet sure
> how to answer. I will restate each one in my own words under "My words" before recording.

**1. What happens, in order, when a request hits the API? (`GET /health`)**

1. Uvicorn receives the HTTP request and passes it to the FastAPI app built by `create_app()`
   (`app/main.py`). The lifespan already ran at startup: it created the one Qdrant client
   (`app.state.qdrant`), and the Neon engine exists in `app/db.py`.
2. `RequestIdMiddleware` (`app/observability.py`) runs first because it is the outermost layer.
   It reuses a safe `X-Request-ID` from the caller or makes a new one, stores it in
   `request.state`, and starts a timer.
3. `CORSMiddleware` checks that the browser's origin is allowed (e.g. the React app on port 5173).
4. The router matches `GET /health` to `health()` in `app/routes/health.py`. An unknown path gives a
   404 instead, still in our error shape.
5. FastAPI resolves the dependencies: `SessionDep` calls `get_db()`, which opens a session, and
   `QdrantDep` calls `get_qdrant()`, which returns the shared client.
6. The route calls one service, `health_service.check_health()`. It runs `SELECT 1` on Neon and
   `get_collections()` on Qdrant at the same time (`asyncio.gather`), each with a 3 s timeout.
7. The route returns `HealthResponse`. FastAPI validates it and turns it into JSON with status 200,
   or 503 if a check failed.
8. If anything raised an exception along the way, the matching handler in `main.py` turns it into
   `{"error": {code, message, request_id}}`.
9. On the way out, the middleware adds the `X-Request-ID` header and writes one log line: method,
   path, status, latency_ms, user_id. `get_db()` closes the session.

My words:

**2. Why does the error handler live in `main.py` and not in the services?**

Services should only know business rules, not HTTP. A service says "this document doesn't exist" by
raising `DocumentNotFoundError`; it does not know that means 404. The handler in `main.py` is the
one place that translates domain errors into status codes and the shared error body. This gives:
- **One error shape everywhere**, because only one function builds it (`error_response()`).
- **Reusable services**: the evaluation script can call the same service without FastAPI.
- **Easy changes**: to change a status code or the error format, I edit one place.
- **Clean layering** (rubric M1): routes → services → pipeline, with HTTP only at the edge.

My words:

**3. What would break if `.env` were committed?**

`.env` holds the real secrets: the Neon connection strings, the JWT secret key, the Anthropic API key
and the Langfuse keys. The repo is public, so anyone could:
- read and delete everything in my Neon database using `DATABASE_URL`;
- forge a login token for any user with `JWT_SECRET_KEY`, which breaks auth and per-user isolation;
- spend my money on the Anthropic API with `ANTHROPIC_API_KEY`.

Bots scan GitHub for keys within minutes. Deleting the file later does not help, because it stays in
Git history. The only fix is to rotate every key. A leaked key also costs marks (rubric M13). That is
why `.env` is in `.gitignore`, only `.env.example` with dummy values is committed, and settings use
`SecretStr` so values don't appear in logs or recordings.

My words:

## Extra questions I asked

> Answers written by Claude Code after I asked these questions. I will restate them in my own words.

**4. What is `JWT_SECRET_KEY` for?**

It is the key the server uses to sign login tokens (built in Phase 2).
- At login the server returns a JWT: `header.payload.signature`. The payload holds e.g. the user id
  and expiry time. The signature is computed from the payload plus `JWT_SECRET_KEY` (HMAC-SHA256).
- On every request the server recomputes the signature with its secret. If it matches, the token is
  genuine and unchanged, so the server trusts the user id inside it.
- Without the secret, anyone could forge a token claiming another user's id and read their documents.
- Analogy: a wax seal. Anyone can read the letter, but only the seal's owner can make a valid seal.
- Signed, not encrypted: the payload is only base64, so nothing secret goes inside a token.
- Trade-off: stateless tokens need no database lookup per request (fast), but cannot be revoked
  before they expire, so expiry is short (60 min). Changing the secret logs everyone out.
- It must be long and random so it cannot be brute-forced.

My words:

**5. What do Qdrant and Docker do?**

Qdrant is the vector database: search by meaning.
- In Phase 4 each document is split into chunks; each chunk becomes 1024 numbers (BGE-M3) that
  capture its meaning, plus a keyword vector (BM25). Qdrant stores these with the chunk's text, page,
  `doc_id` and `owner_id`.
- A question is turned into numbers the same way, and Qdrant finds the closest chunks in milliseconds
  (HNSW index). A filter on `owner_id` means users only search their own documents.
- Neon stores accounts and the document list (who owns what, status); Qdrant stores chunks and vectors.
- Why not pgvector in Postgres: Qdrant natively combines dense + keyword search with RRF fusion and
  filtering, which exact codes like `0052` or `SN009` need.

Docker runs Qdrant for us.
- A container packages an app with everything it needs, so the exact pinned version (`v1.20.1`) runs
  with one command, identical on my laptop and on the server, without installing Qdrant on Windows.
- The named volume keeps the vectors on disk across restarts.
- Analogy: a shipping container runs the same on any ship.

My words:

**6. Will these be set up in production too?**

Yes, in Phase 11 on a Linux VPS (4 vCPU / 8 GB) with Docker Compose.

| Piece | Laptop (dev) | Production (VPS) |
|---|---|---|
| API | `uv run fastapi dev` | Docker container `api` |
| Qdrant | Docker container | Docker container, same pinned image, own volume, not exposed to the internet (only the API reaches it over Docker's internal network) |
| Caddy | none | Docker container: HTTPS, serves the built React app, forwards API calls |
| Neon | cloud database | same cloud service, nothing to install; the VPS connects over the internet (5432 not blocked there) |
| `JWT_SECRET_KEY` | dev value | a new random value in the VPS's own `.env`, never reused |
| `HEALTH_TIMEOUT_S` | 10 (slow hotspot) | default 3 (fast link to Singapore) |

To decide in Phase 11: which Neon branch production uses (recommended: a separate `deploy` branch so
demo data is not mixed with dev experiments), and re-running ingestion on the VPS, because vectors are
not copied from the laptop.

My words:
