# Retrieval eval: `structure_hybrid`

Structure-aware chunks, dense + BM25 fused by RRF (do codes need keywords?). Split `tune`, collection `eval_structure_3fa86c58`, k = 5.

| Metric | Value |
| --- | --- |
| hit rate@5 | 0.800 |
| recall@5 | 0.800 |
| MRR | 0.640 |
| answerable questions | 25 |
| latency p50 / p95 (ms) | 414 / 525 |

## Per category

| Category | n | hit@5 | recall@5 | MRR |
| --- | --- | --- | --- | --- |
| exact_code | 4 | 1.000 | 1.000 | 0.833 |
| field_rule | 4 | 0.500 | 0.500 | 0.500 |
| legal_rule | 4 | 0.750 | 0.750 | 0.271 |
| multi_part | 3 | 1.000 | 1.000 | 1.000 |
| reworded_faq | 3 | 0.333 | 0.333 | 0.333 |
| screenshot | 2 | 1.000 | 1.000 | 0.667 |
| table_matrix | 3 | 1.000 | 1.000 | 1.000 |
| unanswerable | 5 | - | - | - |
| version_sensitive | 2 | 1.000 | 1.000 | 0.625 |

## Misses (no labelled source in the top 5)

- **q008** (field_rule) Is buyerNTNCNIC mandatory when the buyer is unregistered?  
  expected di_api_technical_spec_v1.12.pdf p.10; top-1 was faqs.jsonl p.18 (FAQ 18)
- **q012** (field_rule) How can my ERP find out which unit of measure is allowed for a given HS code?  
  expected di_api_technical_spec_v1.12.pdf p.31; top-1 was sales_tax_rules_2006_upto_2026-07-31.pdf p.189 (STR-7)
- **q018** (legal_rule) Who pays for integrating our POS/ERP with FBR?  
  expected sales_tax_rules_2006_upto_2026-07-31.pdf p.107,110; top-1 was faqs.jsonl p.6 (FAQ 6)
- **q038** (reworded_faq) Should our system create the digital invoice when we bill the customer or when they pay?  
  expected faqs.jsonl p.20, sales_tax_act_1990_upto_2026-06-30.pdf p.24; top-1 was faqs.jsonl p.2 (FAQ 2)
- **q039** (reworded_faq) What happens to us if we miss the digital invoicing integration deadline?  
  expected faqs.jsonl p.16, sales_tax_rules_2006_upto_2026-07-31.pdf p.109; top-1 was faqs.jsonl p.13 (FAQ 13)
