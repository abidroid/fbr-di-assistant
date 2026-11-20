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
    # Uploads with more chunks return 202 and are embedded in a background
    # task. Embedding costs ~1 s per chunk on CPU, so 120 keeps a 201 upload
    # around two minutes. Chunks, not pages: the 51-page API spec makes 524
    # chunks while the 11-page SRO 69 makes 26.
    sync_ingest_max_chunks: int = 120
    # Known corpus files (matched by sha256) get their title, authority and
    # version date from here; other uploads count as third-party documents.
    corpus_manifest_path: str = "corpus/manifest.jsonl"
    # Zip-bomb limits for DOCX/PPTX (both are zip files): a tiny upload must not
    # be allowed to expand into gigabytes when it is unpacked.
    max_unzipped_mb: int = 200
    max_zip_ratio: int = 100

    # --- Extraction ---------------------------------------------------------
    # A PDF page with less text than this has no real text layer (a scan or a
    # screenshot), so it goes to OCR or captioning instead of PyMuPDF.
    min_text_chars_per_page: int = 50
    # Share of the page covered by images above which a low-text page is treated
    # as a screenshot and captioned by the small Claude model.
    caption_min_image_ratio: float = 0.4
    # A page with a text layer whose image covers at least this share of it is
    # a scan with OCR text: its font sizes follow the scan and mean nothing.
    scanned_page_image_ratio: float = 0.9
    # A header/footer line repeated on more than this share of pages is removed.
    repeated_line_ratio: float = 0.5
    # Running headers/footers sit in the top/bottom 8% of a PDF page.
    pdf_margin_ratio: float = 0.08
    # Text this much bigger than the body font is a heading (14 pt vs 12 pt body).
    heading_size_ratio: float = 1.1
    # Text at most this share of the body font is a footnote or a footnote
    # marker (8 pt notes, 5-9 pt superscript numbers vs 12 pt body).
    small_font_ratio: float = 0.8
    # Captions run in parallel, but at most this many at once, to stay well
    # under the API rate limit.
    caption_concurrency: int = 4
    # Longest side of the PNG sent for captioning; about what the model reads
    # without downscaling, and small enough to keep image tokens cheap.
    caption_image_px: int = 1500

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
    # Chunk sizes are in tokens; we measure characters and assume ~4 per token.
    # Exact counts do not matter (BGE-M3 accepts 8192 tokens) and this keeps the
    # chunker free of a tokenizer download, so tests stay fast and offline.
    chars_per_token: int = 4
    # A JSON sample stays whole up to this size (it is only useful complete);
    # bigger ones are split like normal text. BGE-M3 reads up to 8192 tokens.
    max_json_chunk_tokens: int = 2000
    # Longest text BGE-M3 reads per chunk. The library default (512) would
    # silently cut off long JSON samples and rules; 2048 covers our largest
    # chunks (2000-token JSON) plus the context header.
    embed_max_tokens: int = 2048
    # Texts the model processes together. Measured on CPU (8 threads): batch 1
    # = 0.99 s/chunk, 4 = 1.41 s, 8 = 1.88 s; a batch is padded to its longest
    # text and a CPU gains nothing from parallel rows. Use 16-32 on a GPU.
    embed_batch_size: int = 1
    # CPU threads for the embedding model; 0 = all cores. torch's default uses
    # half the logical cores, measured 1.9x slower here.
    embed_threads: int = 0
    qdrant_collection: str = "fbr_di_final"
    qdrant_timeout_s: int = 10
    # Attempts per Qdrant call (first try + retries) before answering 503.
    qdrant_max_retries: int = 3
    hnsw_m: int = 16
    hnsw_ef_construct: int = 100

    # --- Retrieval ----------------------------------------------------------
    candidates: int = 30  # prefetch size for each of dense and bm25
    top_k: int = 6  # chunks passed to the LLM after reranking
    # Below this best rerank score we refuse instead of answering (tuned on golden set).
    refusal_threshold: float = 0.30
    # RRF constant: score = sum of 1 / (k + rank). Qdrant's default is 2, which
    # lets a single list's rank 1 dominate; 60 (from the original RRF paper)
    # rewards chunks that rank well in BOTH the dense and the bm25 list.
    rrf_k: int = 60
    # Added to the rerank score (0-1) when a chunk contains an exact code from
    # the question (0052, SN009, 150XF). Large enough to lift a correct row past
    # a near-tie, small enough not to beat a clearly better passage.
    exact_token_boost: float = 0.15
    # Added to the rerank score by source type: when two passages answer the
    # question about equally well, the binding law should come first.
    authority_boosts: dict[str, float] = {
        "law": 0.05,
        "official_guidance": 0.03,
        "faq": 0.03,
        "third_party": 0.0,
    }
    # Longest question + passage the reranker reads. Most chunks are under 300
    # tokens; 1024 covers long rules without the cost of the model's 8192 max.
    rerank_max_tokens: int = 1024
    # Pairs scored per model call. A batch is padded to its longest pair, so on
    # CPU large batches waste work (as measured for embed_batch_size); 8 is a
    # starting point, timed in notebooks/04_retrieve.
    rerank_batch_size: int = 8

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
        fields = ", ".join(".".join(map(str, err["loc"])).upper() for err in exc.errors())
        raise RuntimeError(f"Invalid or missing settings in .env: {fields}") from None


settings = get_settings()
