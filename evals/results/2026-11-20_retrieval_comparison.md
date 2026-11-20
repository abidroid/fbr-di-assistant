# Retrieval comparison (tune split, k = 5)

| Config | What changes | hit@5 | recall@5 | MRR | p50 ms |
| --- | --- | --- | --- | --- | --- |
| `baseline` | Fixed 512-token chunks, dense only (the before number) | 0.720 | 0.660 | 0.451 | 431 |
| `structure_dense` | Structure-aware chunks, dense only (does chunking help?) | 0.800 | 0.720 | 0.640 | 414 |
| `structure_hybrid` | Structure-aware chunks, dense + BM25 fused by RRF (do codes need keywords?) | 0.800 | 0.800 | 0.640 | 414 |
| `final` | Structure-aware, hybrid + reranker + boosts (the chosen system) | 0.920 | 0.880 | 0.753 | 18228 |

## hit@5 per category

| Config | exact_code | field_rule | legal_rule | multi_part | reworded_faq | screenshot | table_matrix | version_sensitive |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `baseline` | 0.750 | 0.500 | 0.500 | 0.667 | 1.000 | 0.500 | 1.000 | 1.000 |
| `structure_dense` | 1.000 | 0.500 | 0.750 | 1.000 | 1.000 | 0.500 | 1.000 | 0.500 |
| `structure_hybrid` | 1.000 | 0.500 | 0.750 | 1.000 | 0.333 | 1.000 | 1.000 | 1.000 |
| `final` | 1.000 | 0.750 | 0.750 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
