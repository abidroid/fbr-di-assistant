"""Evaluation configurations: one YAML file per experiment in ``evals/configs/``.

A config has three parts:

- ``ingestion``: how the corpus is chunked. This decides what is stored in
  Qdrant, so it decides the collection.
- ``retrieval``: which retrieval steps run (a `RetrievalConfig`).
- ``metrics``: the cut-off ``k`` for hit rate, recall and MRR.

The collection name comes from a hash of everything that changes the stored
vectors (ingestion settings, embedding models, corpus manifest). Configs that
differ only in retrieval therefore share one collection, and the slow corpus
embedding runs once per chunking strategy instead of once per config.
"""

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import settings
from app.retrieval import RetrievalConfig

CONFIG_DIR = Path(__file__).parent / "configs"


class IngestionSection(BaseModel):
    """How the corpus is cut into chunks for this config.

    Attributes:
        chunking: ``structure`` (the real system) or ``fixed_512`` (baseline).
        context_header: Prepend "title > section" before embedding.
    """

    model_config = ConfigDict(extra="forbid")

    chunking: Literal["structure", "fixed_512"] = "structure"
    context_header: bool = True


class RetrievalSection(BaseModel):
    """Which retrieval steps run (mirrors `RetrievalConfig`).

    Attributes:
        mode: ``dense``, ``bm25`` or ``hybrid``.
        rerank: Use the cross-encoder.
        exact_boost: Boost exact code matches (needs rerank).
        authority_boost: Boost law over guidance (needs rerank).
        candidates: Results per search before reranking.
        top_k: Chunks kept at the end.
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["dense", "bm25", "hybrid"] = "hybrid"
    rerank: bool = True
    exact_boost: bool = True
    authority_boost: bool = True
    candidates: int = Field(default_factory=lambda: settings.candidates)
    top_k: int = Field(default_factory=lambda: settings.top_k)

    def to_config(self) -> RetrievalConfig:
        """Build the `RetrievalConfig` that `retrieve()` takes.

        Returns:
            The retrieval configuration.

        Raises:
            ValueError: For invalid combinations (boosts without rerank).
        """
        return RetrievalConfig(**self.model_dump())


class MetricsSection(BaseModel):
    """How retrieval is scored.

    Attributes:
        k: Cut-off rank for hit rate@k, recall@k and MRR.
    """

    model_config = ConfigDict(extra="forbid")

    k: int = Field(default=5, ge=1)


class EvalConfig(BaseModel):
    """One evaluated configuration.

    Attributes:
        name: Same as the file name without ``.yaml``.
        description: One line saying what this config tests.
        ingestion: Chunking settings.
        retrieval: Retrieval steps.
        metrics: Scoring settings.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    ingestion: IngestionSection = IngestionSection()
    retrieval: RetrievalSection = RetrievalSection()
    metrics: MetricsSection = MetricsSection()

    @model_validator(mode="after")
    def retrieval_is_valid(self) -> "EvalConfig":
        """Check the retrieval section now, not halfway through a run.

        Returns:
            The validated config.

        Raises:
            ValueError: If `RetrievalConfig` rejects the combination.
        """
        self.retrieval.to_config()
        return self

    def ingestion_hash(self) -> str:
        """Fingerprint everything that changes the stored vectors.

        Returns:
            A sha256 hex digest of the ingestion settings, the embedding models
            and the corpus manifest. Code changes to the chunker are not
            covered; use ``run_eval --reingest`` after changing it.
        """
        manifest = Path(settings.corpus_manifest_path)
        fingerprint = {
            "ingestion": self.ingestion.model_dump(),
            "dense_model": settings.embedding_model,
            "sparse_model": settings.sparse_model,
            "embed_max_tokens": settings.embed_max_tokens,
            "manifest": hashlib.sha256(manifest.read_bytes()).hexdigest()
            if manifest.exists()
            else None,
        }
        return hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()

    def collection_name(self) -> str:
        """Return the Qdrant collection this config's corpus lives in.

        Returns:
            E.g. ``eval_structure_1a2b3c4d``.
        """
        return f"eval_{self.ingestion.chunking}_{self.ingestion_hash()[:8]}"


def load_config(name: str, directory: Path = CONFIG_DIR) -> EvalConfig:
    """Read and validate one config file.

    Args:
        name: Config name, e.g. ``final`` (reads ``final.yaml``).
        directory: Where the YAML files are.

    Returns:
        The validated config.

    Raises:
        FileNotFoundError: If there is no such config.
        ValueError: If the file is invalid or its ``name`` does not match.
    """
    path = directory / f"{name}.yaml"
    if not path.exists():
        known = ", ".join(available_configs(directory))
        raise FileNotFoundError(f"No config '{name}'. Available: {known}")
    config = EvalConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if config.name != name:
        raise ValueError(f"{path.name}: name '{config.name}' does not match the file name")
    return config


def available_configs(directory: Path = CONFIG_DIR) -> list[str]:
    """List the config names in a directory.

    Args:
        directory: Where the YAML files are.

    Returns:
        Sorted names without ``.yaml``.
    """
    return sorted(path.stem for path in directory.glob("*.yaml"))
