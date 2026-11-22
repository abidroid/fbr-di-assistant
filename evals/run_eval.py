"""Run the evaluation with one command.

    uv run python -m evals.run_eval --config final
    uv run python -m evals.run_eval --config all --split tune

For each config: make sure its collection holds the whole corpus (ingested
once, then reused), run every golden question of the split through
``retrieve()``, score hit rate@k, recall@k and MRR with ``evals/metrics.py``,
and write ``evals/results/<date>_<config>.json`` and ``.md``. With more than
one config, a ``<date>_retrieval_comparison.md`` table is written as well.

``--stage full`` runs every question through the same ``answer_question()``
the API uses (retrieval + generation), then grades each answer with
``evals/judge.py`` and writes ``<date>_<config>_full.json`` and ``.md``:

    uv run python -m evals.run_eval --config final --stage full --split tune

Every report has per-language tables and an English-vs-Urdu table over the
translated questions. ``--language ur`` reruns just the Urdu questions and
their English originals, into files tagged ``_ur``:

    uv run python -m evals.run_eval --config final --stage full --split all --language ur
"""

import argparse
import asyncio
import json
import logging
import uuid
from dataclasses import asdict
from datetime import date
from pathlib import Path

from qdrant_client import AsyncQdrantClient

from app.config import settings
from app.exceptions import UpstreamUnavailableError
from app.generation import LLM, ClaudeLLM, leak_markers
from app.guardrails import redact
from app.ingestion.caption import ClaudeCaptioner
from app.ingestion.embed import create_embedders
from app.ingestion.extract import Extractor
from app.observability import setup_tracing, shutdown_tracing, span, trace
from app.retrieval import Reranker, RetrievalResult, Retrievers, create_reranker, retrieve
from app.services.ask_service import answer_question
from app.services.document_service import Pipeline
from evals.answer_report import (
    AnswerResult,
    answer_result_from_dict,
    answer_threshold_sweep,
    answers_markdown,
    summarise_answers,
)
from evals.config import EvalConfig, available_configs, load_config
from evals.golden_set import GoldenQuestion, load_golden_set
from evals.ingest_corpus import EVAL_OWNER_ID, ingest_corpus
from evals.judge import cited_texts, score_answer
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
# A full run makes paid API calls for ~30 minutes; if the network drops, the
# questions already graded are kept here and skipped on the next run.
CHECKPOINT_SUFFIX = "_full.checkpoint.jsonl"
# One pause-and-retry per question rides out a short network outage.
OUTAGE_PAUSE_S = 30


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
    parser.add_argument(
        "--language",
        choices=["all", "en", "ur"],
        default="all",
        help="ur: the Urdu questions plus the English originals they translate (for the "
        "English-vs-Urdu table), so the Urdu results can be rerun cheaply.",
    )
    parser.add_argument(
        "--stage",
        choices=["retrieval", "full"],
        default="retrieval",
        help="retrieval: metrics only (free). full: also answer and judge (calls the API).",
    )
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


def select_questions(split: str, language: str = "all") -> list[GoldenQuestion]:
    """Return the golden questions of a split and language.

    The held-out split is only for the final report: tuning on it would make
    its numbers meaningless.

    Args:
        split: ``tune``, ``heldout`` or ``all``.
        language: ``all``, ``en``, or ``ur`` (Urdu questions plus the English
            originals they translate, so every translation has its pair).

    Returns:
        The questions, in file order.
    """
    questions = load_golden_set()
    if split != "all":
        questions = [q for q in questions if q.split == split]
    if language == "en":
        return [q for q in questions if q.language == "en"]
    if language == "ur":
        originals = {q.translation_of for q in questions if q.language == "ur"}
        return [q for q in questions if q.language == "ur" or q.id in originals]
    return questions


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
        language=question.language,
        translation_of=question.translation_of,
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


async def evaluate_full(
    config: EvalConfig,
    questions: list[GoldenQuestion],
    retrievers: Retrievers,
    llm: LLM,
    checkpoint: Path,
) -> list[AnswerResult]:
    """Answer every question with the real pipeline and grade each answer.

    Each graded question is appended to ``checkpoint`` straight away, and
    questions already in it are not asked again, so a crash halfway costs
    only the question it happened on.

    Args:
        config: The config being evaluated.
        questions: Golden questions.
        retrievers: Embedders, reranker and Qdrant client.
        llm: Model client for answering and judging.
        checkpoint: JSONL file of results graded so far.

    Returns:
        One result per question, in golden-set order.
    """
    done = load_checkpoint(checkpoint)
    if done:
        print(f"  resuming: {len(done)} questions already graded in {checkpoint.name}")
    for number, question in enumerate(questions, start=1):
        if question.id in done:
            continue
        try:
            result = await answer_and_grade(config, question, retrievers, llm)
        except UpstreamUnavailableError:
            print(f"  API unreachable; retrying {question.id} in {OUTAGE_PAUSE_S} s")
            await asyncio.sleep(OUTAGE_PAUSE_S)
            result = await answer_and_grade(config, question, retrievers, llm)
        done[question.id] = result
        with checkpoint.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(result)) + "\n")
        progress = f"[{number:2}/{len(questions)}] {result.verdict:7} {question.id}"
        print(f"  {progress} {question.question[:55]}")
    return [done[question.id] for question in questions]


def load_checkpoint(path: Path) -> dict[str, AnswerResult]:
    """Read the results graded so far.

    Args:
        path: The checkpoint file (may not exist).

    Returns:
        Results by question id.
    """
    if not path.exists():
        return {}
    results = [
        answer_result_from_dict(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {result.retrieval.id: result for result in results}


async def answer_and_grade(
    config: EvalConfig, question: GoldenQuestion, retrievers: Retrievers, llm: LLM
) -> AnswerResult:
    """Answer one golden question with the real pipeline, then grade it.

    Args:
        config: The config being evaluated.
        question: The golden question.
        retrievers: Embedders, reranker and Qdrant client.
        llm: Model client for answering and judging.

    Returns:
        The question's full result.
    """
    # One trace per golden question, tagged so the metrics report can compare
    # configs. A fresh id per run: reruns must not merge into old traces.
    with trace(
        "eval",
        request_id=uuid.uuid4().hex,
        tags=["eval", f"config:{config.name}", f"split:{question.split}"],
        input={"question_id": question.id, "question": question.question},
    ) as root:
        outcome = await answer_question(
            question.question,
            [],
            EVAL_OWNER_ID,
            retrievers,
            llm,
            request_id=root.record.metadata["request_id"],
            config=config.retrieval.to_config(),
            collection=config.collection_name(),
        )
        assert outcome.retrieval is not None  # retrieval always runs without an empty-index check
        response = outcome.response
        contexts = [chunk.text for chunk in outcome.retrieval.chunks]
        # The judge's LLM calls land in this trace too, as llm.* generations.
        with span("judge"):
            score = await score_answer(
                question=question.question,
                expected_answerable=question.answerable,
                reference=question.reference_answer,
                answer=response.answer,
                refused=not response.answerable,
                contexts=contexts,
                cited=cited_texts(response.answer, contexts),
                llm=llm,
            )
        root.update(output={"verdict": score.verdict, "answer": response.answer})
    generation = outcome.generation
    return AnswerResult(
        retrieval=to_result(question, outcome.retrieval, config.metrics.k),
        reference=question.reference_answer,
        standalone_question=outcome.standalone_question,
        answer=response.answer,
        refused=not response.answerable,
        refusal_reason=generation.refusal_reason if generation else "below_threshold",
        citations=[f"{c.filename} p.{c.page_start}" for c in response.citations],
        faithfulness=score.faithfulness,
        correctness=score.correctness,
        verdict=score.verdict,
        verdict_reason=score.reason,
        citations_supported=score.citations_supported,
        unsupported_claims=score.unsupported_claims,
        generation_ms=response.timings_ms.generation,
        total_ms=response.timings_ms.total,
        # response.language comes from the answer's script (generation code),
        # so this catches a model that ignores rule 7 and answers in English.
        language_match=None if not response.answerable else response.language == question.language,
    )


def write_full_report(
    config: EvalConfig, split: str, results: list[AnswerResult], stamp: str, tag: str = ""
) -> dict[str, object]:
    """Save the full stage's JSON and Markdown results.

    Args:
        config: The evaluated config.
        split: The split used.
        results: Per-question results.
        stamp: Date prefix for the file names.
        tag: File-name suffix for a partial run (e.g. ``_ur``), so it never
            overwrites the report of a full run.

    Returns:
        The answer summary.
    """
    summary = summarise_answers(results)
    sweep = answer_threshold_sweep(results)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "config": config.model_dump(),
        "split": split,
        "date": stamp,
        "settings": {
            "llm_model": settings.llm_model,
            "answer_effort": settings.answer_effort,
            "judge_model": settings.judge_model,
            "refusal_threshold": settings.refusal_threshold,
        },
        "summary": summary,
        "retrieval_summary": summarise([result.retrieval for result in results]),
        "threshold_sweep": sweep,
        "questions": [asdict(result) for result in results],
    }
    base = RESULTS_DIR / f"{stamp}_{config.name}{tag}_full"
    base.with_suffix(".json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    markdown = answers_markdown(
        config.name,
        split,
        settings.judge_model,
        summary,
        sweep,
        results,
        settings.refusal_threshold,
    )
    base.with_suffix(".md").write_text(markdown, encoding="utf-8")
    return summary


def write_config_report(
    config: EvalConfig, split: str, results: list[QuestionResult], stamp: str, tag: str = ""
) -> dict[str, object]:
    """Save one config's JSON and Markdown results.

    Args:
        config: The evaluated config.
        split: The split used.
        results: Per-question results.
        stamp: Date prefix for the file names.
        tag: File-name suffix for a partial run (see `write_full_report`).

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
    base = RESULTS_DIR / f"{stamp}_{config.name}{tag}"
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
    questions = select_questions(args.split, args.language)
    stamp = date.today().isoformat()
    tag = "" if args.language == "all" else f"_{args.language}"
    print(
        f"{len(questions)} {args.split} questions (language {args.language}), "
        f"configs: {[c.name for c in configs]}"
    )

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

        if args.stage == "full":
            # Same tracing as the API (no-op without Langfuse keys), with the
            # same leak redaction before anything leaves the machine.
            setup_tracing(redact=lambda text: redact(text, leak_markers()))
            llm = ClaudeLLM()
            for config in configs:
                print(f"Answering and judging {config.name}")
                checkpoint = RESULTS_DIR / f"{config.name}_{args.split}{tag}{CHECKPOINT_SUFFIX}"
                answers = await evaluate_full(config, questions, retrievers, llm, checkpoint)
                answer_summary = write_full_report(config, args.split, answers, stamp, tag)
                # The report now holds everything; a new run starts fresh.
                checkpoint.unlink()
                print(f"  -> {answer_summary['overall']}")
            return

        rows = []
        for config in configs:
            print(f"Evaluating {config.name}")
            results = await evaluate_retrieval(config, questions, retrievers)
            summary = write_config_report(config, args.split, results, stamp, tag)
            rows.append((config.name, config.description, summary))
            overall = summary["overall"]
            print(f"  -> {overall}")

        if len(rows) > 1:
            k = configs[0].metrics.k
            path = RESULTS_DIR / f"{stamp}{tag}_retrieval_comparison.md"
            path.write_text(comparison_markdown(args.split, k, rows), encoding="utf-8")
            print(path.read_text(encoding="utf-8"))
    finally:
        # Send the last batch of eval traces before the process exits.
        shutdown_tracing()
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
