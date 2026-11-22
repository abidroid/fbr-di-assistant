# Learning Log: Phase 10 (Multilingual / Urdu)

Concepts: a multilingual embedding model (BGE-M3) puts questions in different languages near the
same meaning, so an Urdu question finds English chunks without a translation step; BM25 only
matches exact tokens, so across languages it helps only with codes and field names written in Latin
script (0052, SN017, buyerNTNCNIC); the reranker (bge-reranker-v2-m3) is multilingual too, but
scores Urdu-vs-English pairs lower than English-vs-English; the model writes the Urdu answer
(prompt rule 7), while code adds the fixed texts (refusal, disclaimer, off-topic, empty index) in
the question's language from `Settings.localised`; the reported `language` comes from the text's
script (`detect_language`), never from the model's claim; a paired comparison (each Urdu question
translates an English one, same split and labels) makes "English vs Urdu" a fair measurement.

Measured on 2026-11-22, retrieval stage, `final` config, the 8 translated pairs (k = 5):

| Asked in | hit@5 | recall@5 | MRR |
| --- | --- | --- | --- |
| English original | 1.000 | 0.929 | 0.857 |
| Urdu translation | 1.000 | 0.929 | 0.786 |

Failure found: q055 (Urdu version of the screenshot question q033) has the right chunk at rank 1,
but its best rerank score is 0.019, under `REFUSAL_THRESHOLD` 0.03, so the live system refuses it
(false refusal). The English q033 scores 0.072. Rerank scores are lower for Urdu in most pairs
(q051 0.047 vs q001 0.108). The threshold was not lowered: it was tuned on the English tune split,
and lowering it for one question would let more unanswerable questions through.

**1. Why can BGE-M3 match an Urdu question to an English chunk without translation?**

My words: _(to add before the video)_

Fuller answer (from review): BGE-M3 was trained on text in 100+ languages, including pairs of
sentences that mean the same thing in two languages, and training pulled each pair to the same
point in its 1024-dimensional space. So the vector depends on meaning, not on the alphabet: "ایرر
0052 کا کیا مطلب ہے؟" and "What does error 0052 mean?" end up close together, and close to the
English chunk that answers them, so cosine similarity finds it. Translating first would add a
model call (latency, cost) and could change codes or legal terms. The measured result: Urdu hit@5
equals English on the same 8 questions (1.000); only MRR is a little lower (0.786 vs 0.857), so
the right chunk is sometimes rank 2 instead of rank 1.

**2. The model writes the Urdu answer, so why does code add the refusal and disclaimer?**

My words: _(to add before the video)_

Fuller answer (from review): Fixed texts must be exact. The frontend, the tests and the evaluation
recognise a refusal by its known sentence, and the legal disclaimer must always be there word for
word. A model could paraphrase it, translate it differently each time, or forget it. So code picks
the text from a small `{en, ur}` map in `config.py`, falling back to English for any other
language. The language comes from the script (`detect_language`), not from the model's `language`
field: if the model said "ur" but wrote English, the user would read English with an Urdu
disclaimer. The blocked (400) message stays English like every other API error body.

**3. Why compare English and Urdu only on translated pairs, not "all English vs all Urdu"?**

My words: _(to add before the video)_

Fuller answer (from review): The two groups would contain different questions. If the Urdu group
happened to have easier questions, Urdu would look better for reasons that have nothing to do with
language. With pairs, both halves are the same question with the same split, category and source
labels (`check_translations` enforces this when loading), so the only difference is the language.
q050, which was written in Urdu from the start, is counted in "per language" but not in the pair
table, because it has no English original.
