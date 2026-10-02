"""Deterministic entity extraction when LLM intent omits facets (colors, category, material).

Merges rule-based signals into FashionIntent only for empty fields — never overwrites LLM output.
"""
from __future__ import annotations

from dataclasses import replace

from app.core.logging import get_logger
from app.utils.commerce_semantics import (
    extract_color_lemmas_from_text,
    extract_material_lemmas_from_text,
    fold_el,
)
from app.utils.intent import FashionIntent

logger = get_logger(__name__)

# (keyword_folded, ...), store category name — longest keyword match wins per text.
_CATEGORY_RULES: list[tuple[tuple[str, ...], str]] = [
    (("ολοσωμη", "ολοσωμο", "jumpsuit"), "Ολόσωμες Φόρμες"),
    (("φορεμα", "φορεματα", "dress", "dresses"), "Φορέματα"),
    (("τοπ", "top", "tops", "μπλουζα", "μπλούζα"), "Τοπ"),
    (("παντελονι", "παντελόνια", "trousers", "pants", "jeans"), "Παντελόνια"),
    (("σορτς", "shorts", "βερμουδα"), "Σορτς"),
    (("φουστα", "φούστα", "φούστες", "skirt"), "Φούστες"),
    (("πουκαμισο", "πουκάμισα", "shirt", "shirts"), "Πουκάμισα"),
    (("σακακι", "σακάκι", "blazer", "jacket"), "Σακάκια"),
    (("ζακετα", "ζακέτα", "cardigan"), "Ζακέτες"),
    (("πλεκτο", "πλεκτά", "knit"), "Πλεκτά"),
    (("παλτο", "παλτό", "coat"), "Παλτό"),
    (("μπουφαν", "μπουφάν", "puffer"), "Μπουφάν"),
    (("μαγιο", "μαγιό", "swimsuit", "bikini"), "Μαγιό"),
    (("σανδαλι", "σανδάλια", "sandals"), "Σανδάλια"),
    (("πεδιλα", "πέδιλα"), "Πέδιλα"),
    (("γοβα", "γόβες", "heels"), "Γόβες"),
    (("μποτακι", "μποτάκια", "ankle", "chelsea"), "Μποτάκια"),
    (("μποτα", "μπότες", "boots"), "Μπότες"),
    (("εσπαντριγιες", "εσπαντρίγιες", "espadrilles"), "Εσπαντρίγιες"),
    (("sneakers", "αθλητικο", "αθλητικα", "trainer", "trainers"), "Παπούτσια"),
    (("παπουτσια", "παπούτσια", "shoes", "loafers"), "Παπούτσια"),
    (("τσαντα", "τσάντα", "τσαντες", "τσάντες", "bag", "bags", "clutch"), "Τσάντες"),
    (("φακελος", "φάκελος", "φακελοι"), "Φάκελοι"),
    (("κολιε", "κολιέ", "necklace"), "Κολιέ"),
    (("γυαλια", "γυαλιά", "sunglasses"), "Γυαλιά Ηλίου"),
    (("ζωνη", "ζώνη", "belt"), "Ζώνες"),
    (("καπελο", "καπέλα", "hat"), "Καπέλα"),
]

_OCCASION_RULES: list[tuple[tuple[str, ...], str]] = [
    (("δουλεια", "γραφειο", "office", "work"), "δουλειά"),
    (("γαμο", "γάμος", "wedding", "νυφικο"), "γάμος"),
    (("βραδυ", "βραδι", "evening", "βραδινη"), "βράδυ"),
    (("διακοπες", "vacation", "resort"), "διακοπές"),
]


def extract_store_category_from_query(text: str) -> str | None:
    """Pick ONE store category by longest keyword match in folded text."""
    folded = fold_el(text)
    best: str | None = None
    best_len = 0
    for keywords, cat in _CATEGORY_RULES:
        for kw in keywords:
            if kw in folded and len(kw) > best_len:
                best = cat
                best_len = len(kw)
    return best


def extract_occasion_from_query(text: str) -> str | None:
    folded = fold_el(text)
    best: str | None = None
    best_len = 0
    for keywords, occ in _OCCASION_RULES:
        for kw in keywords:
            if kw in folded and len(kw) > best_len:
                best = occ
                best_len = len(kw)
    return best


def apply_entity_fallback(query: str, intent: FashionIntent) -> FashionIntent:
    """Fill missing colors/materials/category/occasion/search_query from deterministic rules."""
    q = (query or "").strip()
    if not q:
        return intent

    colors = list(intent.colors) if intent.colors else []
    if not colors:
        colors = extract_color_lemmas_from_text(q)

    materials = list(intent.materials) if intent.materials else []
    if not materials:
        materials = extract_material_lemmas_from_text(q)

    target = intent.target_category
    if not target:
        target = extract_store_category_from_query(q)

    occasion = intent.occasion
    if not occasion:
        occasion = extract_occasion_from_query(q)

    sq = (intent.search_query or "").strip()
    if not sq or len(sq) < 2:
        sq = q

    out = replace(
        intent,
        colors=colors,
        materials=materials,
        target_category=target,
        occasion=occasion,
        search_query=sq,
    )

    if (
        colors != (intent.colors or [])
        or materials != (intent.materials or [])
        or target != intent.target_category
        or occasion != intent.occasion
        or sq != (intent.search_query or "").strip()
    ):
        logger.info(
            "entity_fallback applied query=%r colors=%s materials=%s category=%r occasion=%r",
            q[:200],
            out.colors,
            out.materials,
            out.target_category,
            out.occasion,
        )

    return out
