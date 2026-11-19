"""Build faqs.jsonl: one record per FBR Digital Invoicing FAQ (text kept verbatim)."""

import json
from pathlib import Path

SOURCE = "FBR Digital Invoicing FAQs"
SOURCE_URL = "https://www.fbr.gov.pk/faqs/173967/173969"
RETRIEVED = "2026-11-11"
DISCLAIMER = (
    "Disclaimer: This FAQs section is just an effort to have a ready reference and to facilitate all "
    "the Stakeholders of Digital Invoicing. The original Sales Tax Act 1990 and Sales Tax Rules 2006, "
    "shall always prevail in case of any contradiction/error herein. This information shall never be "
    "produced as a legal document before any Court of law / legal forum nor can be used for any "
    "statutory proceedings."
)

# (number, question, answer, topics, legal_refs, links)
FAQS = [
    (
        1,
        "WHAT IS AN ELECTRONIC INVOICE?",
        "A tax invoice created digitally using electronic tools on a specified format is recognized as a structured electronic invoice. However, if a paper invoice is transformed into an electronic one through copying, scanning, or any other method, it doesn't qualify as an electronic invoice.",
        ["definition"],
        [],
        [],
    ),
    (
        2,
        "WHAT IS ELECTRONIC INVOICING?",
        "It's a setup that changes the old-fashioned method of creating paper invoices and notes into a digital and electronic process. This digital process makes it easy for buyers and sellers to exchange invoices, debit notes, and credit notes in a structured electronic format.",
        ["definition"],
        [],
        [],
    ),
    (
        3,
        "IS ELECTRONIC INVOICING MANDATORY? WHO IS REQUIRED TO INTEGRATE?",
        "Yes, as per S.R.O 709 dated 22th April, 2025 of the Sales Tax, Electronic Invoicing is mandatory for all the Corporate registered and Non-Corporate registered persons.",
        ["mandate", "applicability"],
        ["SRO 709(I)/2025"],
        [],
    ),
    (
        4,
        "WHAT IS THE ENFORCEMENT DATE FOR ELECTRONIC INVOICING?",
        "As per S.R.O 709 dated 22th April, 2025 of the Sales Tax, Date of Registration / integration for all the Corporate registered persons is June 1st, 2025 and for all the Non-Corporate registered persons it is July 1st, 2025",
        ["mandate", "deadlines"],
        ["SRO 709(I)/2025"],
        [],
    ),
    (
        5,
        "WHO IS A LICENSED INTEGRATOR?",
        "As per Section 2(15A) “licensed integrator” means any person licensed by the Board to provide electronic invoicing system for integration of registered persons in the prescribed manner; A licensed integrator is an entity licensed under Chapter XIV of the Sales Tax Rules, 2006, to carry out the integration of the registered persons with FBR for the purposes of electronic invoicing.",
        ["licensed integrator", "definition"],
        ["Sales Tax Act 1990 s.2(15A)", "Sales Tax Rules 2006 Chapter XIV"],
        [],
    ),
    (
        6,
        "IS IT MANDATORY TO INTEGRATE MY POS, ERP OR INVOICING SYSTEM THROUGH LICENSED INTEGRATOR?",
        "Yes, for registered persons notified by FBR, it is mandatory to integrate their Point of Sale (POS), Enterprise Resource Planning (ERP) or any other invoicing system with FBR through a licensed integrator having a valid FBR License for Integration. However, in order to provide facilitation and mitigate the hardships faced by the registered persons, it is intimated that as per the Rule l50XF of Chapter XIV of Sales Tax Rules 2006, PRAL shall act as licensed integrator for the purposes of rules l50XE, sub-rule (1) of rule l50XJ and rule 150XL and shall provide free of cost integration services to the registered persons on demand.",
        ["licensed integrator", "PRAL", "integration"],
        [
            "Sales Tax Rules 2006 r.150XE",
            "Sales Tax Rules 2006 r.150XF",
            "Sales Tax Rules 2006 r.150XJ(1)",
            "Sales Tax Rules 2006 r.150XL",
        ],
        [],
    ),
    (
        7,
        "WHERE TO FIND A LICENSED INTEGRATOR?",
        "Updated list of valid licensed integrators is available here. https://fbr.gov.pk/list-of-license-interprator/173967/173971",
        ["licensed integrator"],
        [],
        ["https://fbr.gov.pk/list-of-license-interprator/173967/173971"],
    ),
    (
        8,
        "IS THERE ANY FEE TO BE PAID TO FBR BY REGISTERED PERSON OR LICENSED INTEGRATOR?",
        "No, a registered person liable to install the electronic invoicing system through a licensed integrator or any licensed integrator shall not be liable to pay any fee to FBR. However, the licensed integrator shall charge fee for configuration and integration of electronic invoicing software or point of sale software from the intermated persons not above the threshold as may be specified by the Board through a sales tax general order.",
        ["fees", "licensed integrator"],
        ["Sales Tax General Order (fee threshold)"],
        [],
    ),
    (
        9,
        "IS PHYSICAL VISIT TO FBR IS REQUIRED FOR INTEGRATION?",
        "No, Physical visit is not required for integration. Technical resources and help material will be available on FBR website.",
        ["integration", "procedure"],
        [],
        [],
    ),
    (
        10,
        "HOW TO DOWNLOAD THE SOFTWARE, IF IS IT AVAILABLE ONLINE?",
        "There is no downloadable software. Technical documentation for integration is available for download here. https://fbr.gov.pk/di-technical-assistance/173967/173970",
        ["software", "technical documentation"],
        [],
        ["https://fbr.gov.pk/di-technical-assistance/173967/173970"],
    ),
    (
        11,
        "DOES TAXPAYER REQUIRE ANY COMMERCIAL SOFTWARE FOR INTEGRATION?",
        "Yes, registered persons notified by the Federal Board of Revenue may install any electronic invoicing software and integrate it with FBR through a licensed integrator having a valid license granted by FBR.",
        ["software", "licensed integrator"],
        [],
        [],
    ),
    (
        12,
        "WHO CAN CONFIGURE THE SOFTWARE?",
        "Only a licensed integrator having a valid FBR license can configure a registered person’s electronic invoicing software for real time electronic transmission of sales tax invoice to FBR’s server.",
        ["software", "licensed integrator"],
        [],
        [],
    ),
    (
        13,
        "WHAT IF TAXPAYER INTEGRATES AFTER NOTIFIED DATE?",
        "Registered persons notified to implement electronic invoicing may seek extension under the relevant rules. Taxpayers are subject to provisions of Sales Tax Act 1990 and Rules made there under. Any taxpayer that does not integrate by the extended deadline or contravenes any provision of this Act shall be liable to penal action under section 33 of the Act.",
        ["deadlines", "extension", "penalties"],
        ["Sales Tax Act 1990 s.33"],
        [],
    ),
    (
        14,
        "ARE THERE ANY PENALTIES FOR LICENSED INTEGRATORS FOR NOT BEING ABLE TO PROVIDE THE DESIRED SERVICE?",
        "Yes, licensed integrators are subject to provisions of Sales Tax Act 1990 and Rules made there under. Any licensed integrator, who contravenes any provision of this Act shall be liable to penal action under section 33 of the Act.",
        ["penalties", "licensed integrator"],
        ["Sales Tax Act 1990 s.33"],
        [],
    ),
    (
        15,
        "IN CASE OF BUSINESS INTERRUPTION, ON PART OF TAXPAYER WHAT WOULD BE THE RESPONSIBILITY OF THE TAXPAYER AND LI FOR REPORTING DURING INTERRUPTIONS SUCH AS: Invoice issued on a particular date but goods could not be dispatched due to transportation issue; Invoice issued, goods dispatched on a particular date but the internet system at part of registered person or LI of FBR gets/remains down for a day or more time.",
        "Registered persons and LI shall ensure that in all such eventualities, provisions of the Sales Tax Act 1990 and Sales Tax Rules 2006 are followed.",
        ["business interruption", "downtime"],
        ["Sales Tax Act 1990", "Sales Tax Rules 2006"],
        [],
    ),
    (
        16,
        "ARE THERE ANY PENALTIES FOR REGISTERED PERSONS NOT INTEGRATING WITHIN THE DESIRED TIMELINE?",
        "Registered Persons failing to comply with the Board’s timelines issued for integration are liable to penalties defined in the section 33 of the Act",
        ["penalties", "deadlines"],
        ["Sales Tax Act 1990 s.33"],
        [],
    ),
    (
        17,
        "How can an E-invoice be cancelled?",
        "As per Section 9 of the Sales Tax Act 1990, Where a registered person has issued a tax invoice in respect of a supply made by him and as a result of cancellation of supply, or return of goods, or a change in the nature of supply, or change in the value of the supply, or any event such that the amount shown in the tax invoice or the return needs to be modified, the registered person may, subject to such conditions and limitations as the Board may impose, issue a debit or credit note.",
        ["cancellation", "debit note", "credit note"],
        ["Sales Tax Act 1990 s.9"],
        [],
    ),
    (
        18,
        "If an integrated enterprise makes sales to an unregistered buyer avoids to disclose its particulars like CNIC, business address etc. How will that invoice be processed electronically?",
        "As per Section 23(1)(b) of the Sales Tax Act 1990, in case of supplies by manufacturer or importer to an unregistered distributor, the tax invoice shall contain the NIC or NTN of such unregistered distributors, as the case may be.",
        ["unregistered buyer", "invoice contents"],
        ["Sales Tax Act 1990 s.23(1)(b)"],
        [],
    ),
    (
        19,
        "I intend to integrate with FBR’S E-invoicing system please explain a complete procedure for e-integration?",
        "The user manual for the complete step by step process can be accessed here: https://download1.fbr.gov.pk/Docs/20254171643756444DI-User-Manual.pdf",
        ["integration", "procedure", "user manual"],
        [],
        ["https://download1.fbr.gov.pk/Docs/20254171643756444DI-User-Manual.pdf"],
    ),
    (
        20,
        "In sales tax return, the criteria for reporting of sales tax invoices is based on the definition of “time of supply” i.e. earlier of advance receipt or actual sale. Should the definition of “time of supply” be changed in the law so that the time of taxable supply coincides with “sale” i.e. invoicing?",
        "Digital invoices shall be issued at the time of supply i.e., receipt of payment or delivery of goods, whichever is earlier, as provided under Section 2(44) of the Sales Tax Act, 1990",
        ["time of supply", "sales tax return"],
        ["Sales Tax Act 1990 s.2(44)"],
        [],
    ),
    (
        21,
        "Multiple SKUs can share the same HS code but differ in sale/purchase types, which directly impacts the applicable tax rate. Example: Product Name: RAFHAN AMB DSRT MANGO JELLY PQ5 96X80G, HS Code: 2007.9900, Sale/Purchase Type: Goods at standard rate (default). Product Name: STRAWBERRY CUSTARD & STRAWBERRY JELLY 24X380G, HS Code: 2007.9900, Sale/Purchase Type: 3rd Schedule Goods. Clubbing them together would result in incorrect tax treatment and reporting inconsistencies.",
        "Items having same HS Code but having different Stock Keeping Units (SKUs) can be seamlessly added as distinct items in an invoice using a unique description for the item. In the above example, both of the products have the same HS Code 2007.9900 but their description (deriving out of their product name) is completely unique and different.",
        ["HS code", "SKU", "sale type", "invoice items"],
        [],
        [],
    ),
    (
        22,
        "I have local sales as well as export sales. Does e-invoicing apply to local sales only?",
        "You are required to issue a digital invoice for all sales to be reported in Annexure-C of sales tax return",
        ["applicability", "exports", "sales tax return"],
        ["Sales Tax Return Annexure-C"],
        [],
    ),
]

# Known quality issues, kept verbatim in the text but flagged for the README / failure analysis.
NOTES = {
    3: ["'22th' typo in source"],
    4: ["'22th' typo in source"],
    6: ["'l50XF'/'l50XE'/'l50XJ' use letter l for digit 1 (150XF etc.) - affects exact-match/BM25"],
    8: ["'intermated' is a source typo for 'intimated'"],
    18: [
        "answer covers unregistered distributors, not buyers withholding particulars - partial answer"
    ],
}


def main(out_path: Path) -> None:
    """Write every FAQ, plus the disclaimer record, as one JSON line each to out_path."""
    with out_path.open("w", encoding="utf-8") as f:
        for n, q, a, topics, refs, links in FAQS:
            rec = {
                "id": f"fbr-di-faq-{n:02d}",
                "faq_number": n,
                "question": q,
                "answer": a,
                "text": f"Q: {q}\nA: {a}",  # the string to embed / index
                "metadata": {
                    "source": SOURCE,
                    "source_url": SOURCE_URL,
                    "doc_type": "faq",
                    "authority": "faq",  # law > faq > third_party; Act and Rules prevail per disclaimer
                    "publisher": "FBR",
                    "retrieved": RETRIEVED,
                    "citation": f"FBR DI FAQs, Q{n}",
                    "topics": topics,
                    "legal_refs": refs,
                    "links": links,
                    "quality_notes": NOTES.get(n, []),
                },
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.write(
            json.dumps(
                {
                    "id": "fbr-di-faq-disclaimer",
                    "faq_number": None,
                    "question": None,
                    "answer": None,
                    "text": DISCLAIMER,
                    "metadata": {
                        "source": SOURCE,
                        "source_url": SOURCE_URL,
                        "doc_type": "disclaimer",
                        "authority": "faq",
                        "publisher": "FBR",
                        "retrieved": RETRIEVED,
                        "citation": "FBR DI FAQs, Disclaimer",
                        "topics": ["disclaimer"],
                        "legal_refs": ["Sales Tax Act 1990", "Sales Tax Rules 2006"],
                        "links": [],
                        "quality_notes": [],
                    },
                },
                ensure_ascii=False,
            )
            + "\n"
        )


if __name__ == "__main__":
    main(Path(__file__).with_name("faqs.jsonl"))
