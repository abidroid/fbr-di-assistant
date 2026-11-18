"""Application settings: every constant, threshold and secret lives here.

Values come from environment variables or the `.env` file (pydantic-settings).
Other modules import `settings` and never read `os.environ` directly, so tuning
a value (for example the refusal threshold) means changing exactly one place.
"""

import secrets
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed configuration for the whole application.

    Field names map to upper-case environment variables (``database_url`` reads
    ``DATABASE_URL``). Secrets use ``SecretStr`` so they print as ``**********``
    in logs, tracebacks and screen recordings.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # "KEY=" counts as unset, so a blank required secret fails at startup
        # with "field required" instead of a confusing connection error later.
        env_ignore_empty=True,
        # Unknown keys in .env (e.g. notes for other tools) must not crash startup.
        extra="ignore",
    )

    # --- Secrets and connection strings -------------------------------------
    database_url: SecretStr
    # Optional here so the app runs without it; the test suite requires it.
    test_database_url: SecretStr | None = None
    jwt_secret_key: SecretStr
    anthropic_api_key: SecretStr
    qdrant_url: str = "http://localhost:6333"
    # Only needed for a hosted Qdrant; the local Docker container has no key.
    qdrant_api_key: SecretStr | None = None
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    # --- Auth ---------------------------------------------------------------
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # --- Files and upload limits --------------------------------------------
    upload_dir: str = "uploads"
    max_upload_mb: int = 25
    # Larger files return 202 and finish ingesting in a background task.
    sync_ingest_max_pages: int = 150

    # --- Ingestion: embeddings, chunking, Qdrant index ----------------------
    embedder: Literal["local", "api"] = "local"
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    sparse_model: str = "Qdrant/bm25"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    chunk_size: int = 500
    chunk_overlap: int = 75
    # The fixed_512 strategy exists only as the evaluation baseline.
    fixed_chunk_size: int = 512
    fixed_chunk_overlap: int = 50
    qdrant_collection: str = "fbr_di_final"
    qdrant_timeout_s: int = 10
    hnsw_m: int = 16
    hnsw_ef_construct: int = 100

    # --- Retrieval ----------------------------------------------------------
    candidates: int = 30  # prefetch size for each of dense and bm25
    top_k: int = 6  # chunks passed to the LLM after reranking
    # Below this best rerank score we refuse instead of answering (tuned on golden set).
    refusal_threshold: float = 0.30

    # --- LLM ----------------------------------------------------------------
    llm_model: str = "claude-sonnet-5-5"
    small_model: str = "claude-haiku-5-5"
    llm_timeout_s: int = 30
    llm_max_retries: int = 3
    # Random marker in the system prompt; if it ever appears in an answer, the
    # prompt leaked. A fresh value per process is fine because it is never stored.
    prompt_canary: str = Field(default_factory=lambda: f"CANARY-{secrets.token_hex(8)}")

    # --- API ----------------------------------------------------------------
    rate_limit: str = "20/minute"
    cors_origins: list[str] = ["http://localhost:5173"]
    log_level: str = "INFO"
    # A health check must answer quickly, so each dependency gets a short timeout.
    health_timeout_s: float = 3.0


@lru_cache
def get_settings() -> Settings:
    """Build the settings once and reuse the same object afterwards.

    Returns:
        The cached `Settings` instance.

    Raises:
        RuntimeError: If a setting is missing or invalid. Only field names are
            reported, because pydantic's own message echoes the raw input values,
            which would print real secrets on screen.
    """
    try:
        return Settings()  # type: ignore[call-arg]  # required fields come from .env
    except ValidationError as exc:
        fields = ", ".join(
            ".".join(map(str, err["loc"])).upper() for err in exc.errors()
        )
        raise RuntimeError(f"Invalid or missing settings in .env: {fields}") from None


settings = get_settings()
