"""Ensure chat `answer` shown to users is natural language only — never raw JSON or fences."""
from __future__ import annotations

import json
import re

# Lines that look like LLM chain-of-thought (strip from visible copy)
_REASONING_LINE_STARTS = (
    "the user ",
    "the user wants",
    "i'll ",
    "i will ",
    "i need to ",
    "first, i",
    "step 1",
    "step 2",
    "reasoning:",
    "thought:",
    "analysis:",
    "note: i",
    "let me ",
    "here's my",
    "json output",
    '"message":',
    "'message':",
    '"products":',
)


def _looks_like_json_object(s: str) -> bool:
    t = s.strip()
    if not t.startswith("{"):
        return False
    return '"message"' in t or "'message'" in t or '"products"' in t or "'products'" in t


def _unwrap_markdown_fences(s: str) -> str:
    t = s.strip()
    while True:
        m = re.match(r"^```[a-zA-Z0-9]*\s*\n?([\s\S]*?)```\s*$", t)
        if not m:
            break
        t = m.group(1).strip()
    return t


def _try_extract_message_from_json_blob(s: str) -> str | None:
    """If s is JSON with a string `message`, return it."""
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    msg = data.get("message")
    if isinstance(msg, str) and msg.strip():
        return msg.strip()
    return None


def _strip_reasoning_lines(text: str) -> str:
    out: list[str] = []
    for line in text.split("\n"):
        low = line.lower().lstrip()
        if any(low.startswith(p) for p in _REASONING_LINE_STARTS):
            continue
        out.append(line)
    return "\n".join(out).strip()


def strip_embedded_code_fences(text: str) -> str:
    """Remove ``` ... ``` segments mistakenly pasted inside natural text."""
    return re.sub(r"```(?:json)?\s*[\s\S]*?```", "", text, flags=re.IGNORECASE).strip()


def sanitize_visible_assistant_text(text: str | None, *, lang: str) -> str:
    """Turn model output into safe user-visible chat text. Returns '' if unusable."""
    if not text or not str(text).strip():
        return ""
    t = str(text).strip()
    t = _unwrap_markdown_fences(t)
    if _looks_like_json_object(t):
        extracted = _try_extract_message_from_json_blob(t)
        t = extracted if extracted is not None else ""
    t = strip_embedded_code_fences(t)
    t = _strip_reasoning_lines(t)
    t = t.strip()
    if _looks_like_json_object(t):
        extracted = _try_extract_message_from_json_blob(t)
        t = extracted if extracted is not None else ""
    t = strip_embedded_code_fences(t).strip()
    return t


def fallback_product_intro(lang: str) -> str:
    if lang == "el":
        return (
            "Βρήκα μερικές επιλογές που ταιριάζουν με αυτό που ψάχνεις. "
            "Ρίξε μια ματιά στις προτάσεις παρακάτω — αν θέλεις, πες μου να το πάω πιο casual, πιο elegant ή με άλλο budget."
        )
    return (
        "Here are some picks that match what you're looking for. "
        "Browse the suggestions below — tell me if you want something more casual, more dressy, or a different budget."
    )


def fallback_no_products(lang: str) -> str:
    if lang == "el":
        return (
            "Δεν βρήκα διαθέσιμα προϊόντα που να ταιριάζουν απόλυτα με αυτά τα κριτήρια. "
            "Θες να το ανοίξουμε λίγο (π.χ. χρώμα, budget ή στυλ);"
        )
    return (
        "I couldn't find available products that match those criteria exactly. "
        "Want to widen the search — e.g. colour, budget, or style?"
    )


def degraded_search_notice(lang: str) -> str:
    """Shown when products came from keyword (BM25) fallback instead of semantic search."""
    if lang == "el":
        return (
            "Σημείωση: η έξυπνη αναζήτηση προϊόντων είναι προσωρινά περιορισμένη, "
            "οπότε οι προτάσεις βασίζονται σε αναζήτηση λέξεων-κλειδιών και ίσως είναι λιγότερο ακριβείς."
        )
    return (
        "Note: smart product search is temporarily limited, so these suggestions are based on "
        "keyword matching and may be less precise."
    )
