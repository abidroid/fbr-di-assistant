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
