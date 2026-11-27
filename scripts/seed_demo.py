"""Create the demo user and upload the corpus through the public API.

Runs on the laptop (where the corpus PDFs are) against any running instance:

    uv run python scripts/seed_demo.py --base-url https://203-0-113-5.sslip.io/api --email demo@example.com
    uv run python scripts/seed_demo.py --base-url https://localhost/api --insecure --email demo@example.com

It uses only HTTP, exactly like the browser, so it tests the deployed API and
needs no access to the server's database or files. Safe to run again: an
existing user is reused and already uploaded files come back as 409 and are
skipped. The password comes from DEMO_PASSWORD or a prompt, never a file.
"""

import argparse
import getpass
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

MANIFEST = Path("corpus/manifest.jsonl")
# faqs.jsonl is our own JSON Lines file; the API accepts only document types,
# so it is uploaded as a Markdown file with one heading per question.
FAQ_UPLOAD_NAME = "fbr_di_faqs.md"
MIME_TYPES = {".pdf": "application/pdf", ".md": "text/markdown"}
# Synchronous uploads can take ~2 minutes (embedding on CPU); allow more.
UPLOAD_TIMEOUT_S = 600.0
POLL_INTERVAL_S = 10.0
# The 229-page Sales Tax Act is the slowest background job.
POLL_LIMIT_S = 3600.0


@dataclass
class UploadResult:
    """What happened to one file.

    Attributes:
        filename: Name of the uploaded file.
        outcome: ``ready``, ``failed``, ``duplicate`` or ``timeout``.
        chunks: Chunks stored, when known.
    """

    filename: str
    outcome: str
    chunks: int | None = None


def select_corpus_files(manifest: Path) -> list[Path]:
    """List the corpus files the demo should index, from the manifest.

    Args:
        manifest: Path to ``corpus/manifest.jsonl``.

    Returns:
        Paths of the entries marked ``index: true``, in manifest order.
        Superseded and background documents (``index: false``) are left out,
        as in the evaluation.
    """
    root = manifest.parent
    lines = manifest.read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines if line.strip()]
    return [root / entry["path"] for entry in entries if entry["index"]]


def faqs_to_markdown(records: list[dict]) -> str:
    """Turn the FAQ records into one Markdown document.

    Each question becomes a ``## FAQ n`` heading, so the chunker keeps every
    question with its answer and citations say which FAQ was used.

    Args:
        records: Rows of ``faqs.jsonl`` (``faq_number`` and ``text`` keys;
            the disclaimer row has no number).

    Returns:
        The Markdown text.
    """
    parts = ["# FBR Digital Invoicing FAQs"]
    for record in records:
        number = record.get("faq_number")
        heading = f"## FAQ {number}" if number is not None else "## Disclaimer"
        parts.append(f"{heading}\n\n{record['text'].strip()}")
    return "\n\n".join(parts) + "\n"


def read_upload(path: Path) -> tuple[str, bytes]:
    """Return the name and bytes to upload for one corpus file.

    Args:
        path: A file from `select_corpus_files`.

    Returns:
        ``(filename, content)``; the FAQ JSONL is converted to Markdown.
    """
    if path.suffix == ".jsonl":
        lines = path.read_text(encoding="utf-8").splitlines()
        records = [json.loads(line) for line in lines if line.strip()]
        return FAQ_UPLOAD_NAME, faqs_to_markdown(records).encode("utf-8")
    return path.name, path.read_bytes()


def fail(response: httpx.Response, action: str) -> None:
    """Stop the script with the API's error message.

    Args:
        response: The unsuccessful response.
        action: What was being attempted, for the message.
    """
    try:
        error = response.json()["error"]
        detail = f"{error['code']}: {error['message']}"
    except (ValueError, KeyError, TypeError):
        detail = response.text[:200]
    sys.exit(f"{action} failed ({response.status_code}) {detail}")


def log_in(client: httpx.Client, email: str, password: str) -> None:
    """Create the demo user if needed, log in and store the token on the client.

    Args:
        client: HTTP client pointed at the API.
        email: Demo account email.
        password: Demo account password.
    """
    signup = client.post(
        "/auth/signup", json={"email": email, "full_name": "Demo User", "password": password}
    )
    if signup.status_code == 201:
        print(f"Created user {email}")
    elif signup.status_code == 409:
        print(f"User {email} already exists; logging in")
    else:
        fail(signup, "Signup")
    login = client.post("/auth/login", data={"username": email, "password": password})
    if login.status_code != 200:
        fail(login, "Login")
    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"


def wait_until_done(client: httpx.Client, doc_id: str) -> dict:
    """Poll a document that is being embedded in the background.

    Args:
        client: Logged-in HTTP client.
        doc_id: The document's id.

    Returns:
        The document once its status is ``ready`` or ``failed``, or its last
        state when the time limit is reached.
    """
    deadline = time.monotonic() + POLL_LIMIT_S
    while True:
        response = client.get(f"/documents/{doc_id}")
        if response.status_code != 200:
            fail(response, "Status check")
        document = response.json()
        if document["status"] in ("ready", "failed") or time.monotonic() > deadline:
            return document
        time.sleep(POLL_INTERVAL_S)


def upload(client: httpx.Client, path: Path) -> UploadResult:
    """Upload one corpus file and wait until it is searchable.

    Args:
        client: Logged-in HTTP client.
        path: The corpus file.

    Returns:
        The outcome for the summary.
    """
    filename, content = read_upload(path)
    mime = MIME_TYPES.get(Path(filename).suffix, "application/octet-stream")
    print(f"Uploading {filename} ({len(content) / 1_000_000:.1f} MB)...", flush=True)
    response = client.post(
        "/documents", files={"file": (filename, content, mime)}, timeout=UPLOAD_TIMEOUT_S
    )
    if response.status_code == 409:
        return UploadResult(filename, "duplicate")
    if response.status_code not in (201, 202):
        fail(response, f"Upload of {filename}")
    document = response.json()
    if response.status_code == 202:
        print("  large file: embedding in the background, waiting...", flush=True)
        document = wait_until_done(client, document["id"])
    outcome = document["status"] if document["status"] in ("ready", "failed") else "timeout"
    return UploadResult(filename, outcome, document["chunk_count"])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Read the command line.

    Args:
        argv: Arguments (None = ``sys.argv``).

    Returns:
        The parsed options.
    """
    parser = argparse.ArgumentParser(description="Create the demo user and upload the corpus.")
    parser.add_argument("--base-url", required=True, help="API root, e.g. https://<host>/api")
    parser.add_argument("--email", required=True, help="Demo account email.")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Skip certificate checks (only for Caddy's local certificate on localhost).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Seed the demo account and print a summary.

    Args:
        argv: Arguments (None = ``sys.argv``).
    """
    args = parse_args(argv)
    password = os.environ.get("DEMO_PASSWORD") or getpass.getpass("Demo password: ")
    files = select_corpus_files(MANIFEST)
    missing = [str(path) for path in files if not path.exists()]
    if missing:
        sys.exit("Missing corpus files (see corpus/README.md):\n  " + "\n  ".join(missing))
    with httpx.Client(base_url=args.base_url, verify=not args.insecure, timeout=30.0) as client:
        log_in(client, args.email, password)
        results = [upload(client, path) for path in files]
    print("\nfile | outcome | chunks")
    for result in results:
        print(f"{result.filename} | {result.outcome} | {result.chunks or '-'}")


if __name__ == "__main__":
    main()
