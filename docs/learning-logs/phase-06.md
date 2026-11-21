# Learning Log: Phase 6 (Generation, /ask and the full evaluation)

Concepts: grounded generation from numbered `<document n>` blocks, structured output
(`messages.parse` + Pydantic), citations built by code from chunk metadata, regenerate-once
then refuse, two refusal gates (retrieval threshold without an LLM call, the model's own
`answerable` flag), follow-up rewrite with the small model, the output leak guard (canary,
prompt fragments, API keys, JWTs), faithfulness vs answer correctness (RAGAS-style claims and
factual F1), LLM-as-judge with a different, stronger model, checking the judge against human
labels (agreement and Cohen's kappa), tuning the threshold on real answer outcomes,
checkpointing a paid evaluation run.

**1. How can the model never invent a citation?**

My words: _(to add before the video)_

Fuller answer (from review): The model is never allowed to write a source. It only writes
`[n]` markers, and code turns those into citations.

- **The model only sees numbers.** Retrieval gives 6 chunks, and `build_user_prompt` wraps
  them as `<document n="1">` to `<document n="6">`. The prompt says: cite every claim as `[n]`
  with one of these numbers.
- **Code checks every number.** `extract_citations` finds each `[n]` in the answer and keeps
  it only if `1 <= n <= 6`. An invented `[9]` is deleted from the text (test:
  `test_invented_citation_is_removed`).
- **Code builds the citation.** For each kept `n`, `generate` takes `chunks[n-1]` and copies
  its filename, page_start, page_end and section_path from the Qdrant payload, which was
  written at ingestion from the real PDF. The model never types a file name or a page number,
  so it cannot get one wrong.
- **No valid citation means no answer.** If nothing valid remains, the model gets one more try
  with a reminder. If it still cites nothing, the user gets the refusal (test:
  `test_answer_without_valid_citation_is_regenerated_then_refused`). An uncited claim cannot be
  told apart from a hallucination, so it is never shown.

The limit, to say on video: code guarantees that a citation points at a real passage the model
was given. It cannot guarantee that the passage actually supports the sentence. That is what
the judge's `citations_supported` check measures, and it was 100% on the tune split.

**2. An answer is faithful but wrong: how is that possible?**

My words: _(to add before the video)_

Fuller answer (from review): The two metrics compare the answer against different things.

- **Faithfulness compares the answer with the retrieved chunks.** Did every claim come from
  the passages the model was given? It measures whether the model stuck to the documents.
- **Correctness compares the answer with the reference answer.** Did it say the right thing?

When retrieval brings the wrong chunks, or the right chunks but not the part that holds the
answer, a careful model faithfully repeats what it was given, and the result is wrong.
Examples from our run:

- **q024 (services sector scenarios):** every claim in the answer was true of the table rows it
  received, but it pointed to "All Other Sectors" instead of the reference answer, SN018 and
  SN019. Faithful to the context, wrong for the question.
- **q032 (integrator fees):** it said "the documents give no specific amount" and quoted FAQ 8
  faithfully. But the Rs. 10 per invoice and Rs. 100,000 per month figures are in the corpus,
  just not in the text the model read. Faithful, but only partly correct.
- **q012 (unit of measure per HS code):** the right page (spec p.31) was not retrieved, so the
  model correctly refused. Refusing was honest given what it saw, but it is a false refusal
  for the user.

So the two metrics point to different fixes. Low faithfulness is a **generation** problem: the
model adds facts that are not in the documents, so tighten the prompt. High faithfulness with
low correctness is usually a **retrieval or chunking** problem, so fix what the model is shown.
Our numbers say mostly the second: faithfulness 0.91 overall, but correctness only 0.42 on
table and matrix questions.

**3. What was your judge agreement, and what would you do if it were 60%?**

My words: _(to add before the video)_

Fuller answer (from review): I labelled 20 answers twice (`evals/judge_check.py`, the same
seeded sample both times):

- **Attempt 1: 70% agreement, Cohen's kappa 0.00.** I labelled all 20 "correct", because the
  answers sounded fluent and confident. A rater who always gives the same label carries no
  information, so kappa is exactly 0. The 70% was just the judge's own share of "correct"
  (14/20). In all 6 disagreements the judge was right when checked against the reference. For
  example, q012 was a refusal of a question the corpus can answer, which can never be correct.
- **Attempt 2: 100% agreement, kappa 1.00**, after adding a rubric to the script: compare the
  answer with the reference fact by fact; a refusal is wrong when the reference has an answer.
  Caveat: this attempt was not fully blind. I had seen the judge's reasons for the 6 disputed
  answers. 14 agreements were independent both times.

The lessons: a fluent answer is not a correct one, and a human label is only useful with a
clear rubric and the reference in front of you. Kappa catches a rater who just says yes to
everything, which plain agreement hides.

If agreement were 60%, I would not trust the judge's numbers:

1. **Read the disagreements first.** Who is right, me or the judge? Our attempt 1 showed the
   human can be the problem, not the judge.
2. **If the judge is wrong, fix its instructions.** Give it a clearer definition of
   correct/partly/wrong, tell it to compare against the reference rather than general
   knowledge, and add worked examples of each label to the prompt. Then re-run `judge_check`
   on a fresh sample, because tuning the judge on the same 20 answers would overfit them.
3. **Check for bias.** Does the judge prefer long answers, or its own model family? That is why
   the judge is Opus and the answerer is Sonnet.
4. **Until agreement is high (about 85% or more, kappa above 0.6), report only the human-labelled
   numbers**, or rely on the rule-based metrics (refusal accuracy, citation validity), not the
   judge.
