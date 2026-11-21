"""Check the LLM judge against your own labels.

    uv run python -m evals.judge_check                 # latest *_full.json
    uv run python -m evals.judge_check --file evals/results/2026-11-20_final_full.json

Shows 20 answers from a full-stage run (spread over verdicts and
categories), one at a time: the question, the reference answer and the
system's answer. You type ``c`` (correct), ``p`` (partly) or ``w`` (wrong)
without seeing the judge's verdict, so it cannot bias you. Labels are saved
after every answer (Ctrl+C and run again to continue), then the agreement
is reported as a percentage and as Cohen's kappa.

Why kappa: if the judge said "correct" for everything and most answers are
correct, plain agreement would look high by luck. Kappa subtracts the
agreement expected by chance: 1 = perfect, 0 = no better than chance.
"""

import argparse
import json
import random
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

RESULTS_DIR = Path(__file__).parent / "results"
SAMPLE_SIZE = 20
# A fixed seed, so the same results file always gives the same 20 answers.
SEED = 35
LABELS = {"c": "correct", "p": "partly", "w": "wrong"}
# The same scale the judge uses (evals/judge.py VERDICT_PROMPT). The reference
# answer is the ground truth, so labelling needs no FBR knowledge: compare the
# answer with the reference fact by fact. A fluent answer is not a correct one.
RUBRIC = """\
Compare the ANSWER with the REFERENCE, fact by fact (codes, numbers, limits, names):
  c = correct: has the key facts of the reference and nothing that contradicts it
  p = partly:  some key facts right, but an important one is missing or muddled
  w = wrong:   key facts missing or contradicted, or a refusal when the reference has an answer
Unanswerable questions: a refusal is correct; any other answer is wrong."""


def select_sample(
    questions: Sequence[dict[str, Any]], size: int = SAMPLE_SIZE
) -> list[dict[str, Any]]:
    """Pick answers spread over the judge's verdicts and the categories.

    Taking them round-robin from each verdict group makes sure the rare
    "wrong" and "partly" cases are checked, not just easy correct ones.

    Args:
        questions: The ``questions`` list of a full-stage results file.
        size: How many to pick.

    Returns:
        Up to ``size`` questions in a shuffled, reproducible order.
    """
    rng = random.Random(SEED)
    groups: dict[str, list[dict[str, Any]]] = {}
    for question in questions:
        groups.setdefault(question["verdict"], []).append(question)
    for group in groups.values():
        # Sort by category first, so each group's picks spread over categories.
        group.sort(key=lambda q: (q["retrieval"]["category"], q["retrieval"]["id"]))
        rng.shuffle(group)
    picked: list[dict[str, Any]] = []
    while len(picked) < size and any(groups.values()):
        for name in sorted(groups):
            if groups[name] and len(picked) < size:
                picked.append(groups[name].pop())
    rng.shuffle(picked)
    return picked


def agreement(pairs: Sequence[tuple[str, str]]) -> float:
    """Share of answers where human and judge gave the same label.

    Args:
        pairs: ``(human, judge)`` labels.

    Returns:
        A value from 0 to 1 (0 when there are no pairs).
    """
    return sum(human == judge for human, judge in pairs) / len(pairs) if pairs else 0.0


def cohen_kappa(pairs: Sequence[tuple[str, str]]) -> float:
    """Agreement corrected for chance (Cohen's kappa).

    ``kappa = (observed - expected) / (1 - expected)``, where ``expected`` is
    the agreement two raters would reach by chance given how often each of
    them uses each label.

    Args:
        pairs: ``(human, judge)`` labels.

    Returns:
        Kappa from -1 to 1; 1.0 when both raters always used one same label.
    """
    if not pairs:
        return 0.0
    total = len(pairs)
    human_counts = Counter(human for human, _ in pairs)
    judge_counts = Counter(judge for _, judge in pairs)
    expected = sum(human_counts[label] * judge_counts[label] for label in human_counts) / total**2
    observed = agreement(pairs)
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


def latest_full_results() -> Path:
    """Find the newest full-stage results file.

    Returns:
        Its path.

    Raises:
        SystemExit: If no full-stage run has been saved yet.
    """
    files = sorted(RESULTS_DIR.glob("*_full.json"))
    if not files:
        raise SystemExit("No *_full.json yet: run `python -m evals.run_eval --stage full` first.")
    return files[-1]


def ask_label(question: dict[str, Any], number: int, total: int) -> str:
    """Show one answer and read the user's label.

    Args:
        question: One entry of the results file.
        number: Its position in the sample.
        total: Sample size.

    Returns:
        ``correct``, ``partly`` or ``wrong``.
    """
    retrieval = question["retrieval"]
    print("\n" + "=" * 80)
    print(f"[{number}/{total}] {retrieval['id']} ({retrieval['category']})\n")
    print(RUBRIC)
    print(f"\nQUESTION:  {retrieval['question']}")
    print(f"\nREFERENCE: {question['reference']}")
    print(f"\nANSWER:\n{question['answer']}")
    if question["citations"]:
        print(f"\nCITED: {', '.join(question['citations'])}")
    while True:
        typed = input("\nYour label - c(orrect) / p(artly) / w(rong): ").strip().lower()
        if typed in LABELS:
            return LABELS[typed]


def main(argv: list[str] | None = None) -> None:
    """Label a sample by hand and report the judge's agreement.

    Args:
        argv: Command-line arguments (None = ``sys.argv``).
    """
    parser = argparse.ArgumentParser(description="Check the LLM judge against human labels.")
    parser.add_argument("--file", type=Path, help="A *_full.json results file (default: newest).")
    args = parser.parse_args(argv)
    results_file: Path = args.file or latest_full_results()
    labels_file = results_file.with_name(
        results_file.stem.replace("_full", "_judge_labels") + ".jsonl"
    )
    questions = json.loads(results_file.read_text(encoding="utf-8"))["questions"]
    sample = select_sample(questions)

    done: dict[str, dict[str, str]] = {}
    if labels_file.exists():
        for line in labels_file.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            done[row["id"]] = row
    print(f"Labelling {len(sample)} answers from {results_file.name} ({len(done)} already done).")

    for number, question in enumerate(sample, start=1):
        qid = question["retrieval"]["id"]
        if qid in done:
            continue
        human = ask_label(question, number, len(sample))
        row = {"id": qid, "human": human, "judge": question["verdict"]}
        done[qid] = row
        with labels_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")

    pairs = [(done[q["retrieval"]["id"]]["human"], q["verdict"]) for q in sample]
    print("\n" + "=" * 80)
    print(f"Agreement: {agreement(pairs):.0%} ({sum(h == j for h, j in pairs)}/{len(pairs)})")
    print(f"Cohen's kappa: {cohen_kappa(pairs):.2f}")
    for human, judge in pairs:
        if human != judge:
            print(f"  disagree: you={human:7} judge={judge}")
    print(f"Labels saved to {labels_file}")


if __name__ == "__main__":
    main()
