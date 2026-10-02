"""Soft style refinements: novelty detection, canonical style lemmas for retrieval/rerank.

Additive layer — does not replace commerce_semantics filters; used by chat retrieval.
"""
from __future__ import annotations

from app.utils.commerce_semantics import fold_el

# Folded canonical keys → folded synonym / catalog tokens (Greek + EN).
_STYLE_LEMMA_GROUPS: dict[str, frozenset[str]] = {
    "elegant": frozenset(
        {"elegant", "κομψο", "κομψη", "κομψα", "σικ", "chic", "classy", "κλασικο", "προσεγμενο"}
    ),
    "minimal": frozenset(
        {"minimal", "μινιμαλ", "λιτο", "λιτη", "απλο", "καθαρο", "clean", "απλοτητα"}
    ),
    "casual": frozenset({"casual", "καθημερινο", "χαλαρο", "relaxed", "ανετο"}),
    "sporty": frozenset({"sporty", "αθλητικο", "athletic", "streetwear", "σπορ"}),
    "sexy": frozenset({"sexy", "σεξι", "αποκαλυπτικο", "bodycon", "στενο"}),
    "romantic": frozenset({"romantic", "ρομαντικο", "φλοραλ", "floral", "δαντελα", "lace"}),
    "boho": frozenset({"boho", "bohemian", "εθνικ", "boheme"}),
    "edgy": frozenset({"edgy", "τολμηρο", "rock", "μοντερνο", "statement"}),
    "luxury": frozenset({"luxury", "πολυτελες", "luxurious", "premium", "υψηλης ποιοτητας"}),
    "vintage": frozenset({"vintage", "βινταζ", "retro", "κλασικο"}),
    "office": frozenset({"office", "γραφειο", "επαγγελματικο", "business", "corporate"}),
    "evening": frozenset({"evening", "βραδινο", "βραδυ", "cocktail", "εξοδο"}),
    "unique": frozenset(
        {"ιδιαιτερο", "μοναδικο", "ξεχωριστο", "special", "unique", "statement", "handmade"}
    ),
    "premium": frozenset(
        {"premium", "πολυτελες", "luxury", "designer", "υψηλης ποιοτητας", "υψηλη ποιοτητα"}
    ),
    "budget": frozenset(
        {"οικονομικο", "φθηνο", "affordable", "budget", "προσιτο", "value", "sale"}
    ),
    "statement": frozenset(
        {"statement", "εντυπωσιακο", "τολμηρο", "bold", "eye-catching", "showstopper"}
    ),
}

# Map any folded token to canonical group key.
_TOKEN_TO_GROUP: dict[str, str] = {}
for _key, syns in _STYLE_LEMMA_GROUPS.items():
    _TOKEN_TO_GROUP[_key] = _key
    for s in syns:
        _TOKEN_TO_GROUP[fold_el(s)] = _key


def novelty_requested_from_query(query: str) -> bool:
    """True when the user likely wants alternatives vs the last shown list."""
    q = fold_el(query or "")
    if not q:
        return False
    markers = (
        "κατι αλλο",
        "κατι πιο",
        "διαφορετικ",
        "αλλο στυλ",
        "αλλα προϊοντα",
        "οχι αυτα",
        "οχι τα ιδια",
        "μη τα ιδια",
        "πιο ιδιαιτερο",
        "πιο μοναδικο",
        "αλλου τυπου",
        "something else",
        "something different",
        "different style",
        "not these",
        "show me other",
    )
    return any(m in q for m in markers)


def lemmas_for_style_keywords(style_keywords: list[str]) -> frozenset[str]:
    """Expanded folded lemmas for matching product text (deduped)."""
    out: set[str] = set()
    for raw in style_keywords or []:
        if not (raw or "").strip():
            continue
        f = fold_el(raw.strip())
        if not f:
            continue
        out.add(f)
        grp = _TOKEN_TO_GROUP.get(f)
        if grp:
            out.update(_STYLE_LEMMA_GROUPS.get(grp, frozenset()))
    return frozenset(x for x in out if len(x) >= 3)
