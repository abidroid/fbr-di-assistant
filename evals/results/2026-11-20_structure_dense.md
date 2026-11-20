# Retrieval eval: `structure_dense`

Structure-aware chunks, dense only (does chunking help?). Split `tune`, collection `eval_structure_3fa86c58`, k = 5.

| Metric | Value |
| --- | --- |
| hit rate@5 | 0.800 |
| recall@5 | 0.720 |
| MRR | 0.640 |
| answerable questions | 25 |
| latency p50 / p95 (ms) | 414 / 466 |

## Per category

| Category | n | hit@5 | recall@5 | MRR |
| --- | --- | --- | --- | --- |
| exact_code | 4 | 1.000 | 1.000 | 0.875 |
| field_rule | 4 | 0.500 | 0.500 | 0.500 |
| legal_rule | 4 | 0.750 | 0.750 | 0.458 |
| multi_part | 3 | 1.000 | 1.000 | 0.778 |
| reworded_faq | 3 | 1.000 | 0.500 | 0.667 |
| screenshot | 2 | 0.500 | 0.500 | 0.500 |
| table_matrix | 3 | 1.000 | 1.000 | 1.000 |
| unanswerable | 5 | - | - | - |
| version_sensitive | 2 | 0.500 | 0.250 | 0.167 |

## Misses (no labelled source in the top 5)

- **q008** (field_rule) Is buyerNTNCNIC mandatory when the buyer is unregistered?  
  expected di_api_technical_spec_v1.12.pdf p.10; top-1 was faqs.jsonl p.18 (FAQ 18)
- **q012** (field_rule) How can my ERP find out which unit of measure is allowed for a given HS code?  
  expected di_api_technical_spec_v1.12.pdf p.31; top-1 was sales_tax_rules_2006_upto_2026-07-31.pdf p.189 (STR-7)
- **q018** (legal_rule) Who pays for integrating our POS/ERP with FBR?  
  expected sales_tax_rules_2006_upto_2026-07-31.pdf p.107,110; top-1 was faqs.jsonl p.6 (FAQ 6)
- **q032** (screenshot) How much can a licensed integrator other than PRAL charge us for digital invoicing?  
  expected di_user_manual_v1.5.pdf p.7; top-1 was di_user_manual_v1.5.pdf p.18 (5. Is integration with PRAL mandatory?)
- **q035** (version_sensitive) How many server IP addresses can we enter in the IP whitelisting form, and how long does approval take?  
  expected di_user_manual_v1.5.pdf p.9; top-1 was di_user_manual_v1.5.pdf p.19 (11. How long does IP whitelisting approval take?)
