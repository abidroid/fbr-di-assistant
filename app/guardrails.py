"""Guardrails: checks that protect the assistant from misuse.

Phase 4 adds the document check: text inside an uploaded file that tries to
give the model orders (a hidden "ignore previous instructions") is flagged,
so the chunk can be marked ``suspicious`` and the upload gets a warning.
Flagged text is still stored and searchable; generation treats every
document as data, never as instructions. Input and output checks for /ask
are added in Phase 7.
"""

import re

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
