"""Tests for the pure helpers of scripts/seed_demo.py (no network)."""

import json
from pathlib import Path

from scripts.seed_demo import FAQ_UPLOAD_NAME, faqs_to_markdown, read_upload, select_corpus_files


def write_manifest(tmp_path: Path, entries: list[dict]) -> Path:
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return manifest


def test_select_corpus_files_keeps_only_indexed_entries_in_order(tmp_path: Path) -> None:
    manifest = write_manifest(
        tmp_path,
        [
            {"path": "legal/a.pdf", "index": True},
            {"path": "superseded/old.pdf", "index": False},
            {"path": "faqs.jsonl", "index": True},
        ],
    )
    assert select_corpus_files(manifest) == [tmp_path / "legal/a.pdf", tmp_path / "faqs.jsonl"]


def test_faqs_to_markdown_gives_each_faq_a_heading_and_keeps_the_disclaimer() -> None:
    records = [
        {"faq_number": 1, "text": "Q: What is an e-invoice?\nA: A digital invoice."},
        {"faq_number": None, "text": "Disclaimer: the Act prevails."},
    ]
    markdown = faqs_to_markdown(records)
    assert markdown.startswith("# FBR Digital Invoicing FAQs")
    assert "## FAQ 1\n\nQ: What is an e-invoice?\nA: A digital invoice." in markdown
    assert "## Disclaimer\n\nDisclaimer: the Act prevails." in markdown


def test_read_upload_converts_the_faq_jsonl_to_markdown(tmp_path: Path) -> None:
    faqs = tmp_path / "faqs.jsonl"
    faqs.write_text(json.dumps({"faq_number": 2, "text": "Q: Fee?\nA: No."}) + "\n", "utf-8")
    name, content = read_upload(faqs)
    assert name == FAQ_UPLOAD_NAME
    assert "## FAQ 2" in content.decode("utf-8")


def test_read_upload_sends_other_files_unchanged(tmp_path: Path) -> None:
    pdf = tmp_path / "rules.pdf"
    pdf.write_bytes(b"%PDF-1.7 test")
    assert read_upload(pdf) == ("rules.pdf", b"%PDF-1.7 test")


def test_real_manifest_selects_nine_pdfs_and_the_faqs() -> None:
    files = select_corpus_files(Path("corpus/manifest.jsonl"))
    assert len(files) == 10
    assert not any("superseded" in str(path) for path in files)
