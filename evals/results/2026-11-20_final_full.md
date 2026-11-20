# Answer eval: `final`

Split `tune`, 30 questions, judge `claude-opus-5-5`, refusal threshold 0.03.

| Metric | Value |
| --- | --- |
| faithfulness (answered) | 0.909 |
| answer correctness (factual F1, answerable) | 0.658 |
| judge: correct / partly / wrong | 24 / 4 / 2 |
| accuracy (verdict correct) | 0.800 |
| citation support (answered) | 1.000 |
| unanswerable correctly refused | 100% |
| false refusals (answerable refused) | 4% |

## Per category

| Category | n | faithfulness | correctness | correct | partly | wrong |
| --- | --- | --- | --- | --- | --- | --- |
| exact_code | 4 | 1.000 | 0.964 | 4 | 0 | 0 |
| field_rule | 4 | 0.833 | 0.576 | 3 | 0 | 1 |
| legal_rule | 4 | 1.000 | 0.710 | 4 | 0 | 0 |
| multi_part | 3 | 0.789 | 0.566 | 2 | 1 | 0 |
| reworded_faq | 3 | 1.000 | 0.646 | 2 | 1 | 0 |
| screenshot | 2 | 0.875 | 0.531 | 1 | 1 | 0 |
| table_matrix | 3 | 0.778 | 0.415 | 1 | 1 | 1 |
| unanswerable | 5 | - | - | 5 | 0 | 0 |
| version_sensitive | 2 | 0.938 | 0.748 | 2 | 0 | 0 |

## Refusal threshold sweep (real answer outcomes)

| Threshold | Answerable refused (bad) | Unanswerable refused (good) | Answerable correct |
| --- | --- | --- | --- |
| 0.03 | 4% | 100% | 76% |
| 0.05 | 4% | 100% | 76% |
| 0.10 | 16% | 100% | 68% |
| 0.15 | 20% | 100% | 64% |
| 0.20 | 28% | 100% | 56% |
| 0.25 | 28% | 100% | 56% |
| 0.30 | 28% | 100% | 56% |

## Not correct

- **q012** (field_rule, wrong) How can my ERP find out which unit of measure is allowed for a given HS code?  
  answer: I couldn't find this in your documents.  
  reason: False refusal: the corpus has the answer.
- **q023** (table_matrix, partly) Which scenarios does a steel-sector manufacturer need to test?  
  answer: For Manufacturer in the Steel sector, the spec row lists SN003, SN004, SN011 [2]. Note that the "All Other Sectors" manufacturer row lists SN001, SN002, SN005, SN006, SN007, SN015, SN016, SN017, SN02…  
  reason: The answer correctly gives SN003, SN004 and SN011 for steel manufacturers, but it wrongly suggests they might also need the eleven 'All Other Sectors' scenarios, which undercuts the reference's 'only these three'.
- **q024** (table_matrix, wrong) For a service provider in the services sector, which scenarios apply?  
  answer: The documents do not list a sector named "Services" for Service Provider. The closest entry is "All Other Sectors" (Sr. 91), with scenarios SN001, SN002, SN005, SN006, SN007, SN015, SN016, SN017, SN0…  
  reason: The reference answer is SN018 and SN019, but the answer says no Services sector is listed and points instead to the 13-scenario 'All Other Sectors' list, so it never identifies SN018 and SN019 as the answer.
- **q029** (multi_part, partly) Which sandbox URL do I post invoices to, and how do I send the security token?  
  answer: Post invoices to the sandbox URL https://gw.fbr.gov.pk/di_data/v1/di/postinvoicedata_sb [3]. To validate without posting, use https://gw.fbr.gov.pk/di_data/v1/di/validateinvoicedata_sb [4]. The docum…  
  reason: The answer gives the correct sandbox post URL, but it does not supply the key fact that the token goes in the Authorization header as 'Bearer <token>'; it says instead that the documents do not cover this, which is honest but leaves the answer incomplete.
- **q032** (screenshot, partly) How much can a licensed integrator other than PRAL charge us for digital invoicing?  
  answer: The documents give no specific amount. They say licensed integrators other than PRAL "may charge certain fee" [1][6]. Under FAQ 8, a licensed integrator charges a fee for configuration and integratio…  
  reason: The answer correctly states that PRAL integration is free and that other integrators may charge a fee capped by a Board-specified threshold, but it omits the key figures (Rs. 10 per invoice, Rs. 100,000 monthly retainership, Rs. 1,000,000 annually) and wrongly says no amount is given.
- **q038** (reworded_faq, partly) Should our system create the digital invoice when we bill the customer or when they pay?  
  answer: Neither strictly: digital invoices must be issued at the time of supply, i.e. the receipt of payment or the delivery of goods, whichever is earlier, as provided under Section 2(44) of the Sales Tax A…  
  reason: The answer correctly gives the goods rule (time of supply = earlier of delivery or receipt of payment, section 2(44)), but it omits the reference's point that for services the invoice is due when they are rendered.
