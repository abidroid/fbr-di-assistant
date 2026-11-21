"""Guardrails: checks that protect the assistant from misuse.

Phase 4 adds the document check: text inside an uploaded file that tries to
give the model orders (a hidden "ignore previous instructions") is flagged,
so the chunk can be marked ``suspicious`` and the upload gets a warning.
Flagged text is still stored and searchable; generation treats every
document as data, never as instructions.

Phase 6 adds the output check: an answer that contains the prompt canary, a
piece of the system prompt or anything that looks like a secret is replaced
with a refusal before it leaves the API. Input checks for /ask follow in Phase 7.
"""

import re
from collections.abc import Iterable

# Phrases that address an AI model rather than a reader. Kept narrow on
# purpose: legal text says things like "the Board may issue instructions" or
# "shall act as an agent", which must not be flagged.
SUSPICIOUS_PATTERNS: dict[str, re.Pattern[str]] = {
    "ignore_instructions": re.compile(
        r"\b(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+|your\s+)*"
        r"(previous|prior|above|earlier|preceding)\s+(instructions?|prompts?|rules|context)",
        re.IGNORECASE,
    ),
    "system_prompt": re.compile(r"\bsystem\s+prompt\b", re.IGNORECASE),
    "role_change": re.compile(r"\byou\s+are\s+now\s+(a|an|in|the)\b", re.IGNORECASE),
    "reveal_prompt": re.compile(
        r"\b(reveal|print|show|repeat)\s+(your|the)\s+(system\s+)?(prompt|instructions)\b",
        re.IGNORECASE,
    ),
    "role_tags": re.compile(r"<\s*/?\s*(system|assistant|instructions?)\s*>", re.IGNORECASE),
    "new_instructions": re.compile(
        r"\b(new|updated)\s+instructions\s*(for\s+(the\s+)?(ai|assistant|model))?\s*:",
        re.IGNORECASE,
    ),
}


def find_suspicious(text: str) -> list[str]:
    """Name the instruction-like patterns found in a piece of document text.

    Args:
        text: Chunk text from an uploaded document.

    Returns:
        Names of the matching patterns, e.g. ``["ignore_instructions"]``;
        empty if the text looks like ordinary content.
    """
    return [name for name, pattern in SUSPICIOUS_PATTERNS.items() if pattern.search(text)]


def flag_suspicious(text: str) -> bool:
    """Say whether document text contains instruction-like phrases.

    Args:
        text: Chunk text from an uploaded document.

    Returns:
        True if any suspicious pattern matches.
    """
    return bool(find_suspicious(text))


# --- Output checks -------------------------------------------------------------

# Patterns that must never appear in an answer, whatever the documents say.
LEAK_PATTERNS: dict[str, re.Pattern[str]] = {
    # Anthropic API keys start with "sk-ant-".
    "api_key": re.compile(r"sk-ant-[\w-]{8,}"),
    # A JWT is three base64url parts joined by dots; the first two start with
    # "eyJ" because they are JSON objects ('{"' encodes to "eyJ").
    "jwt": re.compile(r"\beyJ[\w-]{5,}\.eyJ[\w-]{5,}\.[\w-]{10,}"),
}


def check_output(text: str, protected: Iterable[str]) -> str | None:
    """Look for a leak in text the model wrote.

    Args:
        text: The answer (and background) the model produced.
        protected: Strings that must never be repeated: the prompt canary and
            distinctive sentences of the system prompt.

    Returns:
        The name of the leak found (``"prompt"``, ``"api_key"``, ``"jwt"``),
        or None if the text is safe to return.
    """
    folded = text.casefold()
    if any(marker.casefold() in folded for marker in protected if marker):
        return "prompt"
    for name, pattern in LEAK_PATTERNS.items():
        if pattern.search(text):
            return name
    return None
