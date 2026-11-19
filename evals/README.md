# Evaluation: golden set and retrieval metrics

This folder holds the answer key we grade the assistant against, the metric functions,
and (from Phase 5) the eval runner and its dated results.

| File | Purpose |
| --- | --- |
| `golden_set.jsonl` | 50 hand-checked questions, one JSON object per line |
| `golden_set.py` | Pydantic model `GoldenQuestion` and `load_golden_set()`, which validates every line |
| `metrics.py` | Pure functions: `is_hit`, `hit_at_k`, `recall_at_k`, `reciprocal_rank`, `mean_ignoring_none` |
| `tests/unit/test_metrics.py`, `tests/unit/test_golden_set.py` | Unit tests for the metrics and for the file itself |

## Line format

```json
{"id": "q015", "question": "...", "category": "legal_rule", "answerable": true, "language": "en",
 "sources": [{"doc": "sales_tax_rules_2006_upto_2026-07-31.pdf", "pages": [108], "section": "Rule 150S"}],
 "reference_answer": "...", "split": "tune"}
```

- `id`: `q001` to `q050`, unique.
- `category`: one of the nine categories below.
- `answerable`: `false` only for the `unanswerable` category, and those questions have `sources: []`.
- `language`: `en` or `ur` (the assistant must answer, or refuse, in the question's language).
- `sources`: where the answer lives. `doc` is the **file name** from `corpus/manifest.jsonl`
  (no folder). `section` is for humans only; grading uses `doc` + `pages`.
- `reference_answer`: what a correct answer says, used later by the LLM judge.
- `split`: `tune` or `heldout`.

### Page convention: physical pages

`pages` are **physical** PDF pages, 1-based, as PyMuPDF and the Edge page box count them,
not the number printed on the page. They can differ:

| Document | Printed vs physical |
| --- | --- |
| Sales Tax Rules 2006, Chapter XIV | printed (88) to (96) = physical 105 to 113 (physical = printed + 17) |
| DI User Manual v1.5 | printed "Page N of 20" = physical N + 1 |
| DI API technical spec v1.12, Sales Tax Act | printed = physical |
| IRIS dashboard manual | slide number = physical page |

Ingestion (Phase 4) stores the same physical numbers as `page_start` / `page_end`, so labels
and chunks speak the same language.

### FAQ convention: FAQ number as page

`faqs.jsonl` has no pages, so a FAQ source uses its FAQ number: `{"doc": "faqs.jsonl", "pages": [16]}`.
Ingestion sets `page_start = page_end = faq_number` on each FAQ chunk, so the same overlap rule works.

### Why pages and not chunk ids?

Chunk ids change every time the chunking changes. Page labels are independent of chunking,
so the `fixed_512` baseline and the `structure` strategy are graded against the same key.

## Categories and split

| Category | What it tests | Tune | Held-out |
| --- | --- | --- | --- |
| `exact_code` | Error/validation codes such as 0052 or SN009 | 4 | 3 |
| `field_rule` | Rules for one JSON field | 4 | 3 |
| `legal_rule` | A specific rule in Chapter XIV | 4 | 3 |
| `reworded_faq` | A FAQ asked in different words (answer also in the law) | 3 | 2 |
| `table_matrix` | A row of a table (sale type, rate, UoM) | 3 | 2 |
| `multi_part` | Two facts, often from two documents | 3 | 2 |
| `screenshot` | Answer only visible in a manual screenshot | 2 | 1 |
| `version_sensitive` | Answer changed between versions; labelled against the current one | 2 | 1 |
| `unanswerable` | Near-domain but not in the corpus (income tax, other provinces, India) | 5 | 3 |
| **Total** | | **30** | **20** |

**Tune vs held-out.** We tune settings (chunking, `top_k`, `REFUSAL_THRESHOLD`) using only
the 30 `tune` questions, and report final numbers on the 20 `heldout` questions we never
looked at while tuning. Tuning on the same questions we report would give an optimistic score,
like marking yourself with the exam you practised on.

Version-sensitive questions are labelled against the current document only, because
superseded documents are not indexed; the reference answer notes what the old version said.

## Metrics

A retrieved chunk is a **hit** for a source when it comes from the same `doc` and its page
range `page_start..page_end` covers at least one labelled page.

| Metric | Per question | Example: sources {A, B}, only A found, at rank 3 of 5 |
| --- | --- | --- |
| hit@k | 1 if any source is found in the top k, else 0 | 1 |
| recall@k | fraction of sources found in the top k (each source counted once) | 0.5 |
| reciprocal rank | 1 / rank of the first hit, 0 if none | 1/3 |

**Hit rate@k** and **MRR** are the means of hit@k and reciprocal rank over the questions.
Unanswerable questions have no sources, so these functions return `None` for them and
`mean_ignoring_none` leaves them out of the average. They are scored separately, on whether
the assistant refuses.

## Checking the file

```powershell
uv run pytest tests/unit -v
```

`test_golden_set.py` checks the 50 / 30 / 20 counts, the per-category split, and that every
source file is an indexed, current entry in `corpus/manifest.jsonl` with pages in range.
