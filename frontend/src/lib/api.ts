/**
 * Typed functions for every backend endpoint.
 *
 * The types mirror the schemas in the backend's /openapi.json (app/schemas.py).
 * Every request goes through `request()`, so the base URL, the Bearer header
 * and the error handling live in one place.
 */

const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000"
const TOKEN_KEY = "access_token"

// ---------- Types (from /openapi.json) ----------

/** UserRead: an account, never including the password hash. */
export interface User {
  id: string
  email: string
  full_name: string
  created_at: string
}

/** UserCreate: body of POST /auth/signup. */
export interface UserCreate {
  email: string
  full_name: string
  password: string
}

/** Token: body of a successful POST /auth/login. */
export interface Token {
  access_token: string
  token_type: "bearer"
}

/** Ingestion status of a document. */
export type DocumentStatus = "queued" | "processing" | "ready" | "failed"

/** DocumentRead: an uploaded document. Dates are ISO strings. */
export interface DocumentInfo {
  id: string
  filename: string
  file_type: string
  size_bytes: number
  status: DocumentStatus
  pages: number | null
  chunk_count: number
  ocr_pages: number
  warnings: string[]
  error: string | null
  created_at: string
  updated_at: string
}

/** The result of an upload: 201 means ingested, 202 means still embedding. */
export interface UploadResult {
  document: DocumentInfo
  httpStatus: 201 | 202
}

/** HistoryTurn: one earlier message sent with a follow-up question. */
export interface HistoryTurn {
  role: "user" | "assistant"
  content: string
}

/** AskRequest: body of POST /ask. */
export interface AskRequest {
  question: string
  history?: HistoryTurn[]
}

/** Citation: the source behind one [n] marker, built by the backend from metadata. */
export interface Citation {
  n: number
  chunk_id: string
  doc_id: string
  filename: string
  page_start: number
  page_end: number
  section_path: string[]
}

/** RetrievedChunkOut: one chunk the answer was generated from. */
export interface RetrievedChunk {
  n: number
  chunk_id: string
  filename: string
  page_start: number
  section_path: string[]
  text: string
  /** Cosine similarity between the question and the chunk (-1 to 1). */
  similarity: number
  /** Cross-encoder score (0 to 1), or null when reranking is off. */
  rerank_score: number | null
}

/** Timings: milliseconds per pipeline stage. */
export interface Timings {
  guardrails: number
  retrieval: number
  rerank: number
  generation: number
  total: number
}

/** AskResponse: an answer with citations, or a refusal. */
export interface AskResponse {
  request_id: string
  answer: string
  answerable: boolean
  language: string
  citations: Citation[]
  general_context: string | null
  chunk_count: number
  retrieved_chunks: RetrievedChunk[]
  timings_ms: Timings
}

// ---------- Token storage and error handling ----------

/** Read, save and clear the JWT in localStorage so it survives a page reload. */
export const tokenStorage = {
  get: (): string | null => localStorage.getItem(TOKEN_KEY),
  set: (token: string): void => localStorage.setItem(TOKEN_KEY, token),
  clear: (): void => localStorage.removeItem(TOKEN_KEY),
}

/**
 * An error answer from the API, carrying the backend's error shape:
 * `{"error": {"code", "message", "request_id"}}`.
 */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId: string | null

  constructor(status: number, code: string, message: string, requestId: string | null) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.code = code
    this.requestId = requestId
  }
}

// The AuthContext registers what to do on 401 (log out, go to /login). It is
// a callback rather than an import so this file stays free of React code.
let onUnauthorized: () => void = () => {}

/** Register the function called whenever a logged-in request gets a 401. */
export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler
}

/** Turn a failed response into an ApiError, whatever its body looks like. */
async function toApiError(response: Response): Promise<ApiError> {
  const requestId = response.headers.get("X-Request-ID")
  try {
    const body = await response.json()
    if (body?.error?.message) {
      return new ApiError(response.status, body.error.code, body.error.message, body.error.request_id ?? requestId)
    }
  } catch {
    // Not JSON (e.g. a proxy error page); fall through to a generic message.
  }
  return new ApiError(response.status, "http_error", `Request failed (${response.status}).`, requestId)
}

/**
 * Send a request to the API and return the raw response if it succeeded.
 *
 * Adds the base URL and the Bearer token. A network failure (backend down)
 * becomes an ApiError with status 0 so callers handle one error type.
 */
async function send(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  const token = tokenStorage.get()
  if (token) headers.set("Authorization", `Bearer ${token}`)

  let response: Response
  try {
    response = await fetch(`${API_URL}${path}`, { ...init, headers })
  } catch {
    throw new ApiError(0, "network_error", "Cannot reach the server. Is the API running?", null)
  }

  if (!response.ok) {
    // Only a request that sent a token can have an expired session; a wrong
    // password on the login form is also 401 but must not trigger a logout.
    if (response.status === 401 && token) onUnauthorized()
    throw await toApiError(response)
  }
  return response
}

/** Send a request and parse the JSON body as `T`. */
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await send(path, init)
  return (await response.json()) as T
}

/** Send a JSON body with the right Content-Type. */
function jsonBody(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
}

// ---------- Auth ----------

/** POST /auth/signup: create an account (201). */
export function signup(data: UserCreate): Promise<User> {
  return request<User>("/auth/signup", jsonBody("POST", data))
}

/**
 * POST /auth/login: exchange email + password for a JWT.
 *
 * The backend uses OAuth2PasswordRequestForm, which expects a form body
 * with `username` (our email) and `password`, not JSON.
 */
export function login(email: string, password: string): Promise<Token> {
  const form = new URLSearchParams({ username: email, password })
  return request<Token>("/auth/login", { method: "POST", body: form })
}

/** GET /auth/me: the user the stored token belongs to. */
export function getMe(): Promise<User> {
  return request<User>("/auth/me")
}

// ---------- Documents ----------

/** GET /documents: the user's documents, newest first. */
export function listDocuments(): Promise<DocumentInfo[]> {
  return request<DocumentInfo[]>("/documents")
}

/** GET /documents/{id}: one document (used to poll a 202 upload). */
export function getDocument(id: string): Promise<DocumentInfo> {
  return request<DocumentInfo>(`/documents/${id}`)
}

/**
 * POST /documents: upload a file as multipart form data.
 *
 * Returns the HTTP status too, because 201 (ready) and 202 (still embedding)
 * need different messages. The browser sets the multipart Content-Type
 * (with its boundary) itself, so we must not set it.
 */
export async function uploadDocument(file: File): Promise<UploadResult> {
  const form = new FormData()
  form.append("file", file)
  const response = await send("/documents", { method: "POST", body: form })
  const document = (await response.json()) as DocumentInfo
  return { document, httpStatus: response.status === 202 ? 202 : 201 }
}

/** DELETE /documents/{id}: remove the document, its vectors and its file (204). */
export async function deleteDocument(id: string): Promise<void> {
  await send(`/documents/${id}`, { method: "DELETE" })
}

// ---------- Ask ----------

/** POST /ask: ask a question about the user's documents. */
export function ask(data: AskRequest): Promise<AskResponse> {
  return request<AskResponse>("/ask", jsonBody("POST", data))
}
