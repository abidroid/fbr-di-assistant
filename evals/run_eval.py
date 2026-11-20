"""Run the evaluation with one command.

    uv run python -m evals.run_eval --config final
    uv run python -m evals.run_eval --config all --split tune

For each config: make sure its collection holds the whole corpus (ingested
once, then reused), run every golden question of the split through
``retrieve()``, score hit rate@k, recall@k and MRR with ``evals/metrics.py``,
and write ``evals/results/<date>_<config>.json`` and ``.md``. With more than
one config, a ``<date>_retrieval_comparison.md`` table is written as well.

Only ``--stage retrieval`` exists so far; Phase 6 adds ``--stage full``
(answers, faithfulness and correctness).
"""

import argparse
import asyncio
import json
import logging
from dataclasses import asdict
from datetime import date
from pathlib import Path

from qdrant_client import AsyncQdrantClient

from app.config import settings
from app.ingestion.caption import ClaudeCaptioner
from app.ingestion.embed import create_embedders
from app.ingestion.extract import Extractor
from app.retrieval import Reranker, RetrievalResult, Retrievers, create_reranker, retrieve
from app.services.document_service import Pipeline
from evals.config import EvalConfig, available_configs, load_config
from evals.golden_set import GoldenQuestion, load_golden_set
from evals.ingest_corpus import EVAL_OWNER_ID, ingest_corpus
from evals.metrics import RetrievedRef
from evals.report import (
    QuestionResult,
    RetrievedRow,
    comparison_markdown,
    config_markdown,
    score_question,
    summarise,
    threshold_sweep,
)

RESULTS_DIR = Path(__file__).parent / "results"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Read the command line.

    Args:
        argv: Arguments (None = ``sys.argv``).

    Returns:
        The parsed options.
    """
    parser = argparse.ArgumentParser(description="Evaluate retrieval on the golden set.")
    parser.add_argument(
        "--config",
        nargs="+",
        required=True,
        help=f"Config name(s) or 'all'. Available: {', '.join(available_configs())}",
    )
    parser.add_argument("--split", choices=["tune", "heldout", "all"], default="tune")
    parser.add_argument("--stage", choices=["retrieval"], default="retrieval")
    parser.add_argument(
        "--reingest",
        action="store_true",
        help="Drop and rebuild the collections (after changing chunker/extractor code).",
    )
    return parser.parse_args(argv)


def select_configs(names: list[str]) -> list[EvalConfig]:
    """Load the requested configs, keeping the plan's order for 'all'.

    Args:
        names: Names given on the command line.

    Returns:
        The configs to evaluate.
    """
    if names == ["all"]:
        order = ["baseline", "structure_dense", "structure_hybrid", "final"]
        names = [n for n in order if n in available_configs()] + [
            n for n in available_configs() if n not in order
        ]
    return [load_config(name) for name in names]


def select_questions(split: str) -> list[GoldenQuestion]:
    """Return the golden questions of a split.

    The held-out split is only for the final report: tuning on it would make
    its numbers meaningless.

    Args:
        split: ``tune``, ``heldout`` or ``all``.

    Returns:
        The questions.
    """
    questions = load_golden_set()
    return questions if split == "all" else [q for q in questions if q.split == split]


def to_result(question: GoldenQuestion, found: RetrievalResult, k: int) -> QuestionResult:
    """Score one question's retrieval and keep what the report needs.

    Args:
        question: The golden question.
        found: What retrieval returned.
        k: Metric cut-off.

    Returns:
        The question's result.
    """
    refs = [RetrievedRef(c.filename, c.page_start, c.page_end) for c in found.chunks]
    hit, recall, rr, flags = score_question(question, refs, k)
    return QuestionResult(
        id=question.id,
        category=question.category.value,
        answerable=question.answerable,
        question=question.question,
        expected=[f"{s.doc} p.{','.join(map(str, s.pages))}" for s in question.sources],
        hit=hit,
        recall=recall,
        reciprocal_rank=rr,
        best_rerank_score=found.best_rerank_score,
        below_threshold=found.below_threshold,
        latency_ms=round(sum(found.timings_ms.values()), 1),
        retrieved=[
            RetrievedRow(
                rank=chunk.rank,
                filename=chunk.filename,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                section=" > ".join(chunk.section_path),
                similarity=round(chunk.similarity, 4),
                rerank_score=None if chunk.rerank_score is None else round(chunk.rerank_score, 4),
                score=round(chunk.score, 4),
                exact_match=chunk.exact_match,
                hit=flag,
            )
            for chunk, flag in zip(found.chunks, flags, strict=True)
        ],
    )


async def evaluate_retrieval(
    config: EvalConfig, questions: list[GoldenQuestion], retrievers: Retrievers
) -> list[QuestionResult]:
    """Run every question through retrieval with this config.

    Questions run one after another: the models already use every CPU core,
    so running them concurrently would not be faster.

    Args:
        config: The config being evaluated.
        questions: Golden questions.
        retrievers: Embedders, reranker and Qdrant client.

    Returns:
        One result per question.
    """
    retrieval = config.retrieval.to_config()
    collection = config.collection_name()
    results = []
    for number, question in enumerate(questions, start=1):
        found = await retrieve(question.question, EVAL_OWNER_ID, retrievers, retrieval, collection)
        result = to_result(question, found, config.metrics.k)
        results.append(result)
        progress = f"[{number:2}/{len(questions)}] {_mark(result)} {question.id}"
        print(f"  {progress} {question.question[:60]}")
    return results


def write_config_report(
    config: EvalConfig, split: str, results: list[QuestionResult], stamp: str
) -> dict[str, object]:
    """Save one config's JSON and Markdown results.

    Args:
        config: The evaluated config.
        split: The split used.
        results: Per-question results.
        stamp: Date prefix for the file names.

    Returns:
        The summary (for the comparison table).
    """
    summary = summarise(results)
    sweep = threshold_sweep(results)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "config": config.model_dump(),
        "collection": config.collection_name(),
        "split": split,
        "date": stamp,
        "settings": {
            "candidates": config.retrieval.candidates,
            "top_k": config.retrieval.top_k,
            "rrf_k": settings.rrf_k,
            "refusal_threshold": settings.refusal_threshold,
            "exact_token_boost": settings.exact_token_boost,
            "authority_boosts": settings.authority_boosts,
        },
        "summary": summary,
        "threshold_sweep": sweep,
        "questions": [asdict(result) for result in results],
    }
    base = RESULTS_DIR / f"{stamp}_{config.name}"
    base.with_suffix(".json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    markdown = config_markdown(
        config.name,
        config.description,
        split,
        config.collection_name(),
        config.metrics.k,
        summary,
        sweep,
        results,
    )
    base.with_suffix(".md").write_text(markdown, encoding="utf-8")
    return summary


async def main(argv: list[str] | None = None) -> None:
    """Ingest (if needed), evaluate every requested config and write reports.

    Args:
        argv: Command-line arguments (None = ``sys.argv``).
    """
    logging.basicConfig(level=logging.WARNING)
    args = parse_args(argv)
    configs = select_configs(args.config)
    questions = select_questions(args.split)
    stamp = date.today().isoformat()
    print(f"{len(questions)} {args.split} questions, configs: {[c.name for c in configs]}")

    print("Loading models (BGE-M3, BM25" + (", reranker" if _needs_rerank(configs) else "") + ")")
    dense, sparse = await asyncio.to_thread(create_embedders)
    reranker: Reranker = _NoReranker()
    if _needs_rerank(configs):
        reranker = await asyncio.to_thread(create_reranker)
    api_key = settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None
    qdrant = AsyncQdrantClient(url=settings.qdrant_url, api_key=api_key, timeout=60)
    pipeline = Pipeline(
        extractor=Extractor(ClaudeCaptioner()), dense=dense, sparse=sparse, qdrant=qdrant
    )
    retrievers = Retrievers(dense=dense, sparse=sparse, reranker=reranker, qdrant=qdrant)

    try:
        ingested: set[str] = set()
        for config in configs:
            if config.collection_name() not in ingested:
                print(f"Ingesting corpus into {config.collection_name()}")
                await ingest_corpus(config, pipeline, reingest=args.reingest)
                ingested.add(config.collection_name())

        rows = []
        for config in configs:
            print(f"Evaluating {config.name}")
            results = await evaluate_retrieval(config, questions, retrievers)
            summary = write_config_report(config, args.split, results, stamp)
            rows.append((config.name, config.description, summary))
            overall = summary["overall"]
            print(f"  -> {overall}")

        if len(rows) > 1:
            k = configs[0].metrics.k
            path = RESULTS_DIR / f"{stamp}_retrieval_comparison.md"
            path.write_text(comparison_markdown(args.split, k, rows), encoding="utf-8")
            print(path.read_text(encoding="utf-8"))
    finally:
        await qdrant.close()


def _mark(result: QuestionResult) -> str:
    """Short progress label: hit, MISS, or n/a for unanswerable questions."""
    if result.hit is None:
        return "n/a "
    return "hit " if result.hit else "MISS"


def _needs_rerank(configs: list[EvalConfig]) -> bool:
    """True if any config reranks (so the 2 GB reranker is only loaded when used)."""
    return any(config.retrieval.rerank for config in configs)


class _NoReranker:
    """Placeholder when no config reranks; calling it is a bug."""

    async def score(self, query: str, passages: list[str]) -> list[float]:
        """Never called: configs without rerank skip this step."""
        raise RuntimeError("No config asked for reranking, so no reranker was loaded.")


if __name__ == "__main__":
    asyncio.run(main())
