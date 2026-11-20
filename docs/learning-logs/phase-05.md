# Learning Log: Phase 5 (Retrieval)

Concepts: dense (meaning) vs BM25 (exact words) search, hybrid search with two filtered
prefetches, Reciprocal Rank Fusion (ranks, not scores; k = 60), cross-encoder reranking vs
bi-encoder embeddings, exact-token and authority boosts, the refusal threshold and why it is
checked on the raw score, owner and superseded filters inside Qdrant, one retrieval code path
switched by eval configs, sharing one ingested collection between configs, measuring before
tuning (batch size, candidates, threshold).

**1. Why do we fetch 30 candidates but keep only 6?**

My words: _(to add before the video)_

Fuller answer (from review): The two steps have different jobs and different costs.

- **Searching is cheap but rough.** Dense search compares two vectors that were made
  separately (one for the question, one for each chunk), and BM25 only counts words. Both
  take milliseconds, but the right chunk is often at rank 8 or 20 rather than rank 1. So we
  cast a wide net: 30 from dense and 30 from BM25. Recall matters most at this stage, because
  a chunk that is not in the net can never be found later.
- **Reranking is accurate but expensive.** The cross-encoder reads the question and the chunk
  *together*, so it can tell "error 0052 means..." from a chunk that only mentions "error".
  But it costs one model pass per pair, about 1 s on this CPU (measured). So it only sees the
  best 15 fused candidates (`RERANK_CANDIDATES`; we measured 30 → about 60 s per question, too
  slow for `/ask`).
- **The LLM gets only 6.** Every extra chunk costs tokens (money and latency) and gives the
  model more irrelevant text to be distracted by. 6 is enough for multi-part questions
  (two or three sources) with a little margin.

Trade-off: a bigger net raises recall but slows reranking; fewer final chunks are cheaper and
more focused, but a multi-source answer can lose one of its sources.

**2. What does RRF actually add up?**

My words: _(to add before the video)_

Fuller answer (from review): For every chunk, RRF adds `1 / (k + rank)` once for **each list
it appears in**, with k = 60 (`RRF_K`). Only the rank is used, never the raw score.

Worked example: a chunk at dense rank 1 and BM25 rank 3 scores 1/61 + 1/63 = 0.0323. A chunk
at dense rank 2 that BM25 did not find scores 1/62 = 0.0161. The first one wins because
**both** searches liked it.

- **Why ranks:** cosine similarity runs from 0 to 1, while a BM25 score can be 4 or 40
  depending on how rare the words are. Adding them would let BM25 drown out dense. Ranks are
  on the same scale for both lists.
- **Why k = 60:** it flattens the difference between rank 1 and rank 5 (1/61 vs 1/65), so
  agreement between the two lists matters more than winning one of them. Qdrant's default is
  k = 2, where rank 1 (1/3) is worth almost twice rank 3 (1/5), and one list's favourite
  dominates. We set 60 (the value from the original RRF paper) explicitly. Section 4 of
  `notebooks/04_retrieve.ipynb` computes RRF by hand and matches Qdrant's order.

**3. Read your table: which decision earned the biggest gain, and why do you think so?**

My words: _(to add before the video)_

Fuller answer (from review): Tune split, 25 answerable questions, k = 5
(`evals/results/2026-11-20_retrieval_comparison.md`):

| Config | hit@5 | recall@5 | MRR | p50 latency |
| --- | --- | --- | --- | --- |
| baseline (fixed 512, dense) | 0.72 | 0.66 | 0.45 | 0.4 s |
| structure_dense | 0.80 | 0.72 | 0.64 | 0.4 s |
| structure_hybrid (+ BM25, RRF) | 0.80 | 0.80 | 0.64 | 0.4 s |
| final (+ reranker, boosts) | 0.92 | 0.88 | 0.75 | 18 s |

There are two answers, depending on what "gain" means:

- **Structure-aware chunking gave the biggest jump in MRR (+0.19) for free.** It costs
  nothing at question time. With fixed 512-token windows, one window mixes three error codes
  or half of two rules, so its vector is a blur and the right window lands at rank 3–5. With
  one error-code row or one rule per chunk, the vector is about exactly one thing, so it comes
  first. That is also why exact codes were already 1.0 with dense only: each code got its own
  small chunk.
- **The reranker gave the biggest jump in hit rate (+0.12, to 0.92), but at a real price:**
  about 18 s per question on CPU instead of 0.4 s. It rescued the questions where the right
  chunk was in the 30 candidates but below rank 5, e.g. field rules worded differently from
  the spec.
- **BM25 helped recall (+0.08) but not hit rate.** It found the second source of multi-part
  questions. On reworded FAQ questions it hurt (1.00 → 0.33), because the developer's words
  are deliberately not the FAQ's words and keyword matches pulled in other chunks. The
  reranker repaired that (back to 1.00).

Caveats to say on video: with 25 questions, one question moves hit rate by 0.04, so
differences of one or two questions are not strong evidence; the held-out 20 have not been
run yet (they are only for the final report). The two remaining misses: q018 ("who pays for
integration?") retrieved FAQ 6 first, which covers half the answer (PRAL integrates free of
cost under Rule 150XF) but not Rule 150R(10) (the integrated person bears the cost); q012
(allowed unit of measure per HS code) ranked an error-code table above the HS_UOM reference
API page (spec p.31).

The refusal threshold result was a surprise worth explaining: correct answers can have a
low raw rerank score (q017 scored 0.089 and was still answered from Rule 150V), so a
threshold of 0.30 would have refused 24% of answerable questions. Retrieval now only refuses
when it finds essentially nothing (0.03); the LLM's "answerable" check does the rest, and
Phase 6 re-tunes the value on full answers.
