# Learning Log: Phase 8 (Observability)

Concepts: logs vs traces (one line per request vs a timed tree of steps), spans and generations
(an LLM call with model, tokens and cost), p50 vs p95 (the median vs the slow tail users notice),
a no-op tracer so tests and fresh clones need no account, explicit spans instead of `@observe`
so we choose what leaves the server, hashed user ids, cost computed from our own price table,
WARNING (client mistake) vs ERROR (outage or bug) for the error rate, OpenTelemetry context
propagation (a span opened inside another becomes its child, also across `await`).

Measured on 2026-11-22 (one real question about Rule 150V on SRO 69, CPU laptop):
total 31.8 s = rerank 27.7 s + input classifier 4.3 s + answer 3.7 s + query embedding 0.3 s +
search 0.06 s. Answer call 3,376 input / 134 output tokens on Sonnet 5.5 = $0.00809; classifier
620 / 11 tokens on Haiku 5.5 = $0.00007.

**1. Which step is slowest, and why?**

My words: _(to add before the video)_

Fuller answer (from review): Reranking, at about 28 s of a 32 s question (the `rerank` span).
`bge-reranker-v2-m3` is a cross-encoder: it reads the question and each candidate passage
*together* through the whole model, once per pair, so 15 candidates means 15 full model passes on
a CPU (~1-2 s each). The query embedding is fast (0.3 s) because it encodes only the short
question; the chunks were embedded once at upload. Qdrant search takes 57 ms because HNSW is an
index lookup, not a model. The LLM calls (4.3 s classifier + 3.7 s answer) are network waits,
not our CPU. Levers, with their trade-offs: fewer rerank candidates (`RERANK_CANDIDATES`, 15 ->
8 roughly halves it, but the eval must confirm hit rate holds), a GPU (batched pairs, ~1 s
total, but a costlier server), or a hosted reranker API (fast, but a per-call cost and a
dependency). The trace makes this a measured decision instead of a guess.

**2. What does one question cost?**

My words: _(to add before the video)_

Fuller answer (from review): About $0.008 (0.8 US cents) for an answered question: the Sonnet 5.5
answer call is $0.00809 (3,376 input tokens x $2/M + 134 output tokens x $10/M) and the Haiku
classifier adds $0.00007. Input tokens dominate because the prompt carries six retrieved chunks;
output is short. A follow-up adds one Haiku rewrite (~$0.0001). A question refused below the
retrieval threshold skips the Sonnet call and costs only the classifier, and a question blocked by
the regex rules costs nothing. Our code computes the cost from `settings.model_prices` (from
`response.usage`, using the model that actually answered, since a refusal fallback may switch
models) and sends it to Langfuse, so the number can be checked by hand.
`scripts/metrics_report.py` gives the mean and p95 over the last N questions.

**3. Why explicit spans instead of `@observe`, and what never leaves the server?**

My words: _(to add before the video)_

Fuller answer (from review): `@observe` records every function argument and return value
automatically, which would send whole document chunks and the prompt (with the canary) to a
third-party service. With `with span("rerank") as step:` we decide what each span carries:
chunk ids, file names and scores, the verdict, the answer. Never sent: the system prompt, chunk
text, the email (the user id is a SHA-256 pseudonym), tokens or keys. As a second layer, every
string passes through `guardrails.redact` (Langfuse's `mask` hook) before export. We also export
only our own spans (`should_export_span=is_langfuse_span`): the Anthropic SDK emits an
OpenTelemetry span per call too, which would count every token and dollar twice. Without Langfuse
keys the `NoopTracer` makes every span do nothing, so tests run offline.
