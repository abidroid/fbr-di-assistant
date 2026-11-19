# Learning Log: Phase 3 (Golden set and metrics)

Concepts: golden set as an answer key, labels by document + physical page (not chunk id),
hit rate@k, recall@k, reciprocal rank / MRR, unanswerable questions (`None`, scored on refusal),
tune vs held-out split.

**1. A question has 2 correct sources and only 1 is in the top 5. What are hit@5 and recall@5?**

My words: _(to add before the video)_

Fuller answer (from review): hit@5 = 1, because at least one correct source appeared in the
top 5 (hit is pass/fail). recall@5 = 0.5, because 1 of the 2 sources appeared. Hit rate says
whether the model got *something* to answer from; recall says whether it got *everything*. For a
multi-part question, recall 0.5 means half of the answer is missing.

**2. The first correct chunk is at rank 3. What is the reciprocal rank?**

My words: _(to add before the video)_

Fuller answer (from review): 1/3 ≈ 0.33. Reciprocal rank is 1 ÷ position of the first correct
chunk (rank 1 → 1.0, rank 2 → 0.5, none → 0). Its average over questions is MRR. It rewards putting
the right chunk *first*, which matters because the LLM leans most on the top chunks; improving it
is the reranker's job.

**3. Why must the 20 held-out questions stay untouched while tuning?**

My words: _(to add before the video)_

Fuller answer (from review): In Phase 5 we try many settings (chunk size, `top_k`,
`REFUSAL_THRESHOLD`) and keep whichever scores best. If we also report on those same questions,
we have picked settings that suit them, and the score overstates real use (overfitting). So we tune
on the 30 `tune` questions only and run the 20 `heldout` questions once at the end, like an exam
paper you have not seen. The trade-off is less data to tune on, in exchange for a number we can
trust.
