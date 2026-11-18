# Corpus: FBR Digital Invoicing documents

The documents the assistant is built and evaluated on. **The PDFs are not committed to Git**
(the PRAL documents say they must not be redistributed, and all of them are public on FBR's site),
so download them from the sources below into the same paths. `manifest.jsonl` lists every file
with its title, type, authority, version date, page count and SHA-256, so you can check your copy
matches the one used in the evaluation.

| Path | Document | Indexed | Source |
| --- | --- | --- | --- |
| `legal/sales_tax_act_1990_upto_2026-06-30.pdf` | Sales Tax Act, 1990 (to 30.06.2026) | Yes (DI sections) | [FBR](https://download1.fbr.gov.pk/Docs/20267171373418951SalesTaxAct1990updatedupto30.06.2026.pdf) |
| `legal/sales_tax_rules_2006_upto_2026-07-31.pdf` | Sales Tax Rules, 2006 (to 31.07.2026) | Yes (Chapter XIV) | [FBR](https://download1.fbr.gov.pk/Docs/202691810951482SalesTaxRules.pdf) |
| `legal/sro_69(I)_2025_digital_invoicing_rules.pdf` | SRO 69(I)/2025, Digital Invoicing Rules | Yes | [FBR](https://download1.fbr.gov.pk/SROs/2025129141598258SRO69(I)2025.pdf) |
| `legal/sro_709(I)_2025_registered_persons_required_to_integrate.pdf` | SRO 709(I)/2025, persons required to integrate | Yes | [FBR](https://download1.fbr.gov.pk/SROs/2025423124414622SRO709dated22April,2025.pdf) |
| `legal/sro_2071(I)_2025_retailers_integration.pdf` | SRO 2071(I)/2025, Rule 150Q(3) for retailers | Yes | [FBR DI legal provisions](https://www.fbr.gov.pk/di-legal-provisions/173967/173968) |
| `legal/extension_for_integration_2025.pdf` | Extension for Integration (June 2025) | Yes | [FBR](https://download1.fbr.gov.pk/Docs/2025621176112478ExtensionDateIntegration.pdf) |
| `technical/di_api_technical_spec_v1.12.pdf` | DI API Technical Documentation v1.12 (PRAL) | Yes | [FBR DI technical assistance](https://www.fbr.gov.pk/di-technical-assistance/173967/173970) |
| `technical/di_user_manual_v1.5.pdf` | DI User Manual v1.5 (PRAL) | Yes | [FBR DI technical assistance](https://www.fbr.gov.pk/di-technical-assistance/173967/173970) |
| `portal/iris_dashboard_manual.pdf` | IRIS 2.0 Dashboard Manual | Yes (captions) | Provided by the developer |
| `faqs.jsonl` | FBR DI FAQs (22 Q&A + disclaimer) | Yes | [FBR FAQs](https://www.fbr.gov.pk/faqs/173967/173969), built by `scripts/build_faqs.py` |
| `superseded/chapter_xiv_2025-05.pdf` | Chapter XIV, May 2025 scan | No (version experiment) | [FBR](https://download1.fbr.gov.pk/Docs/2025571554338577ChapterXIV.pdf) |
| `superseded/di_user_manual_2025-05.pdf` | DI User Manual, May 2025 | No (superseded) | [FBR](https://download1.fbr.gov.pk/Docs/20254171643756444DI-User-Manual.pdf) |
| `extra/circular_02_2025-26_finance_act_2025_sales_tax.pdf` | Circular 02 of 2025-26, Finance Act 2025 | No (background) | [FBR](https://download1.fbr.gov.pk/Docs/202584118361586CircularNO02of2025-26SalesTax&FederalExcise.pdf) |

Folders: `legal/` laws, rules and notifications · `technical/` PRAL integration documents ·
`portal/` taxpayer portal guide · `superseded/` older versions kept only for the version
experiment · `extra/` background material not indexed by default.
