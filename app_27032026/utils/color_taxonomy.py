"""Canonical color taxonomy for commerce retrieval: hard matching, not fuzzy hue blending.

Greek + English aliases map to stable IDs. Used by hybrid search metadata filter
and post-filter on RAG results so "λευκό" does not match "ροζ" and "κόκκινο" not "μπλε".
"""
from __future__ import annotations

# Canonical id -> lowercase substring aliases (longer phrases first for greedy checks)
COLOR_ALIASES: dict[str, tuple[str, ...]] = {
    "white": (
        "λευκό", "λευκο", "λευκά", "λευκα", "άσπρο", "ασπρο", "άσπρα", "ασπρα",
        "white", "whites",
    ),
    "off_white": ("off-white", "off white", "natural white"),
    "ivory": ("ivory", "αποχρώσεις ivory", "ιβουάρ"),
    "ecru": ("ecru", "έκρου", "εκρου"),
    "cream": ("cream", "κρεμ", "κρέμ", "κρεμ"),
    "beige": ("beige", "μπεζ", "μπέζ"),
    "powder_pink": ("powder pink", "powder-pink", "σκόνη ροζ", "ανοιχτό ροζ"),
    "pink": ("ροζ", "ρόζ", "pink", "ροζέ", "ροζε"),
    "fuchsia": ("fuchsia", "φούξια", "φουξια", "φούξια", "magenta"),
    "red": (
        "κόκκινο", "κοκκινο", "κόκκινα", "κοκκινα", "κόκκινη", "κοκκινη",
        "red", "reds", "scarlet", "κόκκινο χρώμα",
    ),
    "burgundy": ("burgundy", "πορφόρ", "πορφορ", "μπορντό", "μπορντο"),
    "wine": ("wine", "κρασί", "κρασι", "οινόχρωμο"),
    "blue": (
        "μπλε", "blue", "blues", "γαλάζιο", "γαλαζιο", "γαλάζια",
        "azure",
    ),
    "navy": ("navy", "βαθύ μπλε", "βαθυ μπλε", "σκούρο μπλε", "σκουρο μπλε", "ναυτικό"),
    "royal_blue": ("royal blue", "βασιλικό μπλε", "βασιλικο μπλε"),
    "black": (
        "μαύρο", "μαυρο", "μαύρα", "μαυρα", "μάυρα", "μάυρο",  # μάυρα = common typo
        "black", "blacks",
    ),
    "green": ("πράσινο", "πρασινο", "πράσινα", "πρασινα", "green", "khaki", "χακί", "χακι"),
    "yellow": ("κίτρινο", "κιτρινο", "yellow", "mustard", "μουσταρδί"),
    "orange": ("πορτοκαλί", "πορτοκαλι", "orange"),
    "brown": ("καφέ", "καφε", "brown", "ταμπά", "ταμπα", "camel", "κάμελ"),
    "grey": ("γκρι", "γκρί", "grey", "gray", "ανοιχτό γκρι", "σκούρο γκρι"),
    "purple": ("μωβ", "μόβ", "purple", "violet", "βιολετί", "βιολετι"),
    "gold": ("χρυσό", "χρυσο", "gold", "golden"),
    "silver": ("ασημί", "ασημι", "silver"),
}

def _flatten_alias_to_id() -> dict[str, str]:
    out: dict[str, str] = {}
    for cid, aliases in COLOR_ALIASES.items():
        for a in aliases:
            out[a.lower()] = cid
    return out


_ALIAS_TO_ID: dict[str, str] = _flatten_alias_to_id()


def resolve_color_canonical(raw: str | None) -> str | None:
    """Map free text (Greek/English, canonical id, or alias) to canonical color id."""
    if not raw or not str(raw).strip():
        return None
    s = normalize_greek_commerce_query(str(raw).strip()).lower()
    if s in COLOR_ALIASES:
        return s
    if s in _ALIAS_TO_ID:
        return _ALIAS_TO_ID[s]
    # longest alias match inside string
    best: str | None = None
    best_len = 0
    for alias, cid in _ALIAS_TO_ID.items():
        if alias in s and len(alias) > best_len:
            best_len = len(alias)
            best = cid
    return best


def primary_color_alias_for_filter(canonical_id: str) -> str:
    """Single string stored in HybridSearchFilters.color for logging/cache; matching uses taxonomy."""
    aliases = COLOR_ALIASES.get(canonical_id)
    if not aliases:
        return canonical_id
    # Prefer Greek retail term for chunk text match
    for a in aliases:
        if any("\u0370" <= c <= "\u03FF" or "\u1F00" <= c <= "\u1FFF" for c in a):
            return a
    return aliases[0]


def _haystack_for_chunk(name: str, text: str, categories: list[str] | None) -> str:
    parts = [
        name or "",
        text or "",
        " ".join(categories or []),
    ]
    return " ".join(parts).lower()


def chunk_matches_color(
    haystack: str,
    canonical_id: str,
    *,
    strict: bool = True,
) -> bool:
    """True if chunk text matches desired color (positive alias substring match)."""
    del strict  # reserved for future tiered / family relaxation
    hay = haystack.lower()
    aliases = COLOR_ALIASES.get(canonical_id)
    if not aliases:
        return canonical_id.lower() in hay
    return any(a.lower() in hay for a in aliases)


def color_family_alternatives(canonical_id: str) -> tuple[str, ...]:
    """Nearby shades offered only when exact family has zero results."""
    families: dict[str, tuple[str, ...]] = {
        "white": ("ivory", "ecru", "cream", "off_white"),
        "red": ("burgundy", "wine"),
        "blue": ("navy", "royal_blue"),
        "pink": ("powder_pink", "fuchsia"),
    }
    return families.get(canonical_id, ())


def normalize_greek_commerce_query(query: str) -> str:
    """Fix common Greek typos / spelling variants before color/category extraction.

    Example: «μάυρα» (wrong accent) → «μαύρα» so color aliases match.
    """
    if not query:
        return query
    s = query
    # Wrong accent on αύ / αυ is a frequent typo for «μαύρο/μαύρα»
    for wrong, right in (
        ("μάυρ", "μαύρ"),
        ("Μάυρ", "Μαύρ"),
        ("μαυρ", "μαύρ"),
        ("Μαυρ", "Μαύρ"),
    ):
        s = s.replace(wrong, right)
    return s


def extract_color_from_query_text(query: str) -> str | None:
    """Lightweight extraction when LLM omits color field: first alias hit in query."""
    if not query:
        return None
    nq = normalize_greek_commerce_query(query)
    low = nq.lower()
    best: str | None = None
    best_len = 0
    for alias, cid in _ALIAS_TO_ID.items():
        if alias in low and len(alias) > best_len:
            best_len = len(alias)
            best = cid
    return best
