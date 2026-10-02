from __future__ import annotations

import re

_GREEK_RE = re.compile(r"[\u0370-\u03FF\u1F00-\u1FFF]")


def detect_language(text: str) -> str:
    """Return 'el' if text contains Greek characters, otherwise 'en'."""
    greek_chars = len(_GREEK_RE.findall(text))
    total_alpha = sum(1 for c in text if c.isalpha())
    if total_alpha == 0:
        return "en"
    ratio = greek_chars / total_alpha
    return "el" if ratio > 0.3 else "en"


def resolve_language(explicit: str | None, query: str) -> str:
    """Determine language: explicit override > auto-detect."""
    if explicit and explicit.lower() in ("el", "en"):
        return explicit.lower()
    return detect_language(query)
