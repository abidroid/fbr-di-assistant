"""Download the official FBR documents behind the legal_refs in faqs.jsonl.

Run on your own computer (needs Python 3.8+, no extra packages):
    python download_legal_refs.py

Files go into ./legal/ and a manifest.jsonl is written alongside them with
metadata you can attach to every chunk at ingestion time.
"""

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

DOCS = [
    {
        "file": "sales_tax_act_1990_upto_2026-06-30.pdf",
        "title": "Sales Tax Act, 1990 (updated up to 30.06.2026)",
        "url": "https://download1.fbr.gov.pk/Docs/20267171373418951SalesTaxAct1990updatedupto30.06.2026.pdf",
        "covers": [
            "Sales Tax Act 1990 s.2(15A)",
            "Sales Tax Act 1990 s.2(44)",
            "Sales Tax Act 1990 s.9",
            "Sales Tax Act 1990 s.23(1)(b)",
            "Sales Tax Act 1990 s.33",
        ],
        "doc_type": "act",
    },
    {
        "file": "sales_tax_rules_2006_upto_2026-07-31.pdf",
        "title": "The Sales Tax Rules, 2006 (updated up to 31st July, 2026)",
        "url": "https://download1.fbr.gov.pk/Docs/202691810951482SalesTaxRules.pdf",
        "covers": [
            "Sales Tax Rules 2006 Chapter XIV",
            "Sales Tax Rules 2006 r.150XE",
            "Sales Tax Rules 2006 r.150XF",
            "Sales Tax Rules 2006 r.150XJ(1)",
            "Sales Tax Rules 2006 r.150XL",
        ],
        "doc_type": "rules",
    },
    {
        "file": "sro_69(I)_2025_digital_invoicing_rules.pdf",
        "title": "SRO 69(I)/2025 - Digital Invoicing Rules",
        "url": "https://download1.fbr.gov.pk/SROs/2025129141598258SRO69(I)2025.pdf",
        "covers": ["Sales Tax Rules 2006 Chapter XIV"],
        "doc_type": "sro",
    },
    {
        "file": "sro_709(I)_2025_registered_persons_required_to_integrate.pdf",
        "title": "SRO 709(I)/2025 dated 22 April 2025 - Registered Persons Required to Integrate",
        "url": "https://download1.fbr.gov.pk/SROs/2025423124414622SRO709dated22April,2025.pdf",
        "covers": ["SRO 709(I)/2025"],
        "doc_type": "sro",
    },
    {
        "file": "extension_for_integration_2025.pdf",
        "title": "Extension for Integration (FBR, June 2025)",
        "url": "https://download1.fbr.gov.pk/Docs/2025621176112478ExtensionDateIntegration.pdf",
        "covers": ["deadline extension (FAQ 13)"],
        "doc_type": "notification",
    },
    {
        "file": "chapter_xiv_2025-05.pdf",
        "title": "Chapter XIV (FBR, May 2025) - verify contents after download",
        "url": "https://download1.fbr.gov.pk/Docs/2025571554338577ChapterXIV.pdf",
        "covers": ["Sales Tax Rules 2006 Chapter XIV"],
        "doc_type": "rules",
    },
    {
        "file": "di_user_manual.pdf",
        "title": "Digital Invoicing User Manual",
        "url": "https://download1.fbr.gov.pk/Docs/20254171643756444DI-User-Manual.pdf",
        "covers": ["integration procedure (FAQ 19)"],
        "doc_type": "user_manual",
    },
    {
        "file": "circular_02_2025-26_finance_act_2025_sales_tax.pdf",
        "title": "Circular No. 02 of 2025-26 - Finance Act 2025 amendments to Sales Tax Act",
        "url": "https://download1.fbr.gov.pk/Docs/202584118361586CircularNO02of2025-26SalesTax&FederalExcise.pdf",
        "covers": ["Sales Tax Act 1990 (2025 amendments)"],
        "doc_type": "circular",
    },
]


def safe_url(url: str) -> str:
    """Percent-encode characters like spaces, commas, & and parentheses in the path."""
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(p._replace(path=urllib.parse.quote(p.path, safe="/")))


def main() -> int:
    """Download each legal PDF into ./legal and write its manifest.

    Returns:
        Exit code: 1 if any download failed, otherwise 0.
    """
    out = Path("legal")
    out.mkdir(exist_ok=True)
    manifest, failed = [], []
    for d in DOCS:
        dest = out / d["file"]
        if dest.exists() and dest.stat().st_size > 0:
            print(f"skip (exists)  {dest.name}")
        else:
            try:
                req = urllib.request.Request(
                    safe_url(d["url"]), headers={"User-Agent": "Mozilla/5.0"}
                )
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = r.read()
                if not data.startswith(b"%PDF"):
                    raise ValueError("response is not a PDF")
                dest.write_bytes(data)
                print(f"ok  {len(data) / 1e6:5.1f} MB  {dest.name}")
            except Exception as e:  # keep going; report at the end
                print(f"FAILED  {dest.name}: {e}")
                failed.append(d["url"])
                continue
            time.sleep(1)  # be polite to FBR's server
        manifest.append(
            {
                **d,
                "publisher": "FBR",
                "authority": "law"
                if d["doc_type"] in {"act", "rules", "sro"}
                else "official_guidance",
                "retrieved": time.strftime("%Y-%m-%d"),
            }
        )
    with (out / "manifest.jsonl").open("w", encoding="utf-8") as f:
        for m in manifest:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    print(f"\n{len(manifest)} downloaded, {len(failed)} failed. Manifest: {out / 'manifest.jsonl'}")
    for u in failed:
        print("  open manually:", u)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
