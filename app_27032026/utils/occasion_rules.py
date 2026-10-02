"""Map user/LLM occasion phrases to canonical keys and haystack keywords for retrieval."""
from __future__ import annotations

# Canonical key -> Greek/English phrases for substring match in product text + style hints
OCCASION_SYNONYMS: dict[str, tuple[str, ...]] = {
    "wedding": (
        "γάμος", "γαμο", "wedding", "νυφικό", "νυφικο", "καλεσμένη", "καλεσμενη",
        "wedding guest",
    ),
    "summer_wedding": (
        "καλοκαιρινό γάμο", "καλοκαιρινο γαμο", "summer wedding", "γάμο καλοκαίρι",
    ),
    "evening_event": (
        "βραδινό", "βραδινο", "evening", "gala", "επίσημο", "επισημο", "cocktail",
    ),
    "first_date": (
        "πρώτο ραντεβού", "πρωτο ραντεβου", "first date", "ραντεβού",
    ),
    "modest": (
        "σεμνά", "σεμνα", "modest", "σκεπαστά", "σκεπαστα", "μετριοπαθής",
    ),
    "easter_resurrection": (
        "ανάσταση", "ανασταση", "πάσχα", "πασχα", "εκκλησία", "εκκλησια",
        "easter", "resurrection", "λαμπρή", "λαμπρη",
    ),
    "gift": ("δώρο", "δωρο", "gift", "γενέθλια", "γενεθλια"),
}

# Primary filter substring (first match wins for HybridSearchFilters.occasion)
OCCASION_FILTER_KEYWORD: dict[str, str] = {
    "wedding": "γάμος",
    "summer_wedding": "γάμος",
    "evening_event": "βραδιν",
    "first_date": "ραντεβού",
    "modest": "σεμν",
    "easter_resurrection": "εκκλησ",
    "gift": "δώρο",
}


def resolve_occasion_key(raw: str | None) -> str | None:
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip().lower()
    if s in OCCASION_SYNONYMS:
        return s
    for key, syns in OCCASION_SYNONYMS.items():
        if key == s:
            return key
        for syn in syns:
            if syn.lower() in s or s in syn.lower():
                return key
    return None


def occasion_haystack_keyword(key: str | None) -> str | None:
    """Single substring used with filters.occasion for metadata filter."""
    if not key:
        return None
    return OCCASION_FILTER_KEYWORD.get(key)


def occasion_style_keywords(key: str | None) -> tuple[str, ...]:
    """Soft ranking hints for occasion (BM25/haystack overlap). Not user-facing requirements.

    Keep broad and avoid fabric/silhouette words (satin, glam, bodycon) unless the user
    explicitly asked — those must not act as hidden exact-match constraints.
    """
    if key == "wedding":
        return ("elegant", "επίσημο", "νυφικό", "wedding")
    if key == "summer_wedding":
        return ("καλοκαίρι", "ανάλαφρο", "summer")
    if key == "evening_event":
        return ("βραδινό", "evening", "έξοδο", "cocktail")
    if key == "first_date":
        return ("casual", "chic")
    if key == "modest":
        return ("σεμνό", "μίντι", "κάλυψη")
    if key == "easter_resurrection":
        return ("σεμνό", "σκούρο", "εκκλησία")
    if key == "gift":
        return (
            "σετ",
            "set",
            "gift",
            "δώρο",
            "αξεσουάρ",
            "κοσμήμα",
            "καλλυντικ",
            "beauty",
            "lifestyle",
            "πολυτελές",
            "elegant",
        )
    return ()
