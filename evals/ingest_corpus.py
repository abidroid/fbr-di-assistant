"""Ingest the corpus into an evaluation collection.

Uses the same code as a real upload (validate -> extract -> chunk -> embed ->
store), so the evaluation measures the real system, but skips the API and
Neon: no users or rows are needed to search a collection.

Three things keep repeated runs cheap:

- Extraction results are cached per file hash in ``evals/cache/`` (git-ignored),
  so OCR and the paid screenshot captions run once.
- A document whose stored point count already equals its chunk count is
  skipped, so an interrupted run resumes where it stopped.
- Point ids are stable (``doc_id:chunk_index``), so re-ingesting overwrites
  instead of duplicating.
"""

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import anyio
from qdrant_client import AsyncQdrantClient

from app.config import settings
from app.ingestion.chunk import Chunk, chunk_blocks
from app.ingestion.extract import ExtractedBlock, Extractor, load_faq_records
from app.ingestion.store import DocumentMeta, count_document_points, with_retries
from app.ingestion.validate import validate_file
from app.services.document_service import Pipeline, embed_and_store
from evals.config import EvalConfig

logger = logging.getLogger(__name__)

CORPUS_DIR = Path(settings.corpus_manifest_path).parent
CACHE_DIR = Path(__file__).parent / "cache" / "extract"
# All eval chunks belong to one made-up user; retrieval filters on it.
EVAL_OWNER_ID = uuid.UUID("e7a10000-0000-4000-8000-000000000001")
# uuid5(namespace, path) gives every corpus file the same doc id on every run.
DOC_NAMESPACE = uuid.UUID("0b6f3d52-5c2e-4f0e-9a51-6c1f3e7d2a90")
# Chunks embedded per step: gives a progress line about once a minute on CPU.
EMBED_SLICE = 64


@dataclass(frozen=True)
class CorpusDoc:
    """One corpus file, as described by ``corpus/manifest.jsonl``.

    Attributes:
        path: Path relative to ``corpus/``.
        title: Official title (used in the context header).
        authority: ``law``, ``official_guidance`` or ``faq``.
        version_date: Date the text is valid for.
        superseded: True for an outdated version (stored, but filtered out).
        sha256: Expected hash of the file.
    """

    path: str
    title: str
    authority: str
    version_date: str | None
    superseded: bool
    sha256: str | None

    @property
    def filename(self) -> str:
        """File name without folder: what citations and golden labels use."""
        return Path(self.path).name

    @property
    def doc_id(self) -> uuid.UUID:
        """Stable document id derived from the path."""
        return uuid.uuid5(DOC_NAMESPACE, self.path)


def corpus_documents(manifest_path: str | Path = settings.corpus_manifest_path) -> list[CorpusDoc]:
    """List the files the evaluation ingests.

    Indexed files, plus superseded versions: they are stored with
    ``superseded=true`` so the version-sensitive questions prove that the
    retrieval filter really keeps them out.

    Args:
        manifest_path: The corpus manifest.

    Returns:
        The documents in manifest order.
    """
    documents = []
    for line in Path(manifest_path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        superseded = bool(record.get("superseded", False))
        if not (record.get("index") or superseded):
            continue
        documents.append(
            CorpusDoc(
                path=record["path"],
                title=record["title"],
                authority=record.get("authority", "third_party"),
                version_date=record.get("version_date"),
                superseded=superseded,
                sha256=record.get("sha256"),
            )
        )
    return documents


async def extract_blocks(doc: CorpusDoc, extractor: Extractor) -> list[ExtractedBlock]:
    """Extract a corpus file, using the on-disk cache when possible.

    Args:
        doc: The corpus file.
        extractor: The real extractor (OCR and captions included).

    Returns:
        The file's blocks in reading order.

    Raises:
        FileNotFoundError: If the file is missing from ``corpus/``.
    """
    path = CORPUS_DIR / doc.path
    data = path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    if doc.sha256 and sha256 != doc.sha256:
        logger.warning("%s does not match its manifest hash; using the file on disk", doc.path)
    if path.suffix == ".jsonl":
        # FAQ records are already one block each; nothing to extract.
        return load_faq_records(path)

    cache = CACHE_DIR / f"{sha256}.json"
    if cache.exists():
        return [_block_from_json(item) for item in json.loads(cache.read_text("utf-8"))]
    file = await anyio.to_thread.run_sync(validate_file, data, doc.filename)
    result = await extractor.extract(file)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps([asdict(block) for block in result.blocks]), encoding="utf-8")
    return result.blocks


def chunk_document(doc: CorpusDoc, blocks: list[ExtractedBlock], config: EvalConfig) -> list[Chunk]:
    """Chunk a document the way the config says.

    Args:
        doc: The corpus file.
        blocks: Its extracted blocks.
        config: Chunking strategy and context-header switch.

    Returns:
        The chunks.
    """
    return chunk_blocks(
        blocks,
        doc.title,
        strategy=config.ingestion.chunking,
        context_header=config.ingestion.context_header,
    )


async def ingest_corpus(
    config: EvalConfig,
    pipeline: Pipeline,
    reingest: bool = False,
    report: Callable[[str], None] = print,
) -> dict[str, int]:
    """Make sure the config's collection holds the whole corpus.

    Args:
        config: The evaluation config (decides chunking and collection).
        pipeline: Extractor, embedders and Qdrant client.
        reingest: Drop the collection first and embed everything again
            (needed after changing chunker or extractor code).
        report: Where progress lines go.

    Returns:
        Chunk count per file name.
    """
    collection = config.collection_name()
    if reingest:
        await _drop_collection(pipeline.qdrant, collection)
    counts: dict[str, int] = {}
    for doc in corpus_documents():
        blocks = await extract_blocks(doc, pipeline.extractor)
        chunks = await anyio.to_thread.run_sync(chunk_document, doc, blocks, config)
        counts[doc.filename] = len(chunks)
        stored = await count_document_points(pipeline.qdrant, doc.doc_id, collection)
        if stored == len(chunks):
            report(f"  {doc.filename}: {len(chunks)} chunks already in {collection}")
            continue
        await _embed_document(doc, chunks, pipeline, collection, report)
    return counts


async def _embed_document(
    doc: CorpusDoc,
    chunks: list[Chunk],
    pipeline: Pipeline,
    collection: str,
    report: Callable[[str], None],
) -> None:
    """Embed and store one document in slices, printing progress.

    Args:
        doc: The corpus file.
        chunks: Its chunks.
        pipeline: Embedders and Qdrant client.
        collection: Target collection.
        report: Where progress lines go.
    """
    meta = DocumentMeta(
        doc_id=doc.doc_id,
        owner_id=EVAL_OWNER_ID,
        filename=doc.filename,
        authority=doc.authority,
        version_date=doc.version_date,
        superseded=doc.superseded,
    )
    started = time.perf_counter()
    for start in range(0, len(chunks), EMBED_SLICE):
        await embed_and_store(chunks[start : start + EMBED_SLICE], meta, pipeline, collection)
        done = min(start + EMBED_SLICE, len(chunks))
        elapsed = time.perf_counter() - started
        report(f"  {doc.filename}: embedded {done}/{len(chunks)} ({elapsed:.0f} s)")


async def _drop_collection(qdrant: AsyncQdrantClient, collection: str) -> None:
    """Delete a collection if it exists."""
    if await with_retries(lambda: qdrant.collection_exists(collection), "collection_exists"):
        await with_retries(lambda: qdrant.delete_collection(collection), "delete_collection")


def _block_from_json(item: dict[str, object]) -> ExtractedBlock:
    """Rebuild a cached block (JSON turns tuples into lists).

    Args:
        item: One block as stored in the cache.

    Returns:
        The block.
    """
    return ExtractedBlock(
        text=str(item["text"]),
        page=item["page"],  # type: ignore[arg-type]
        section_path=tuple(item.get("section_path") or ()),  # type: ignore[arg-type]
        kind=item.get("kind", "text"),  # type: ignore[arg-type]
        ocr=bool(item.get("ocr", False)),
        amended_by=tuple(item.get("amended_by") or ()),  # type: ignore[arg-type]
    )
