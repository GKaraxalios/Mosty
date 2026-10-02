"""Greek commerce text normalization and attribute matching for hybrid filters.

Reduces false negatives from tonos/plural mismatch and common synonyms when
metadata filters use substring checks over product text.
"""
from __future__ import annotations

import unicodedata
from typing import Iterable

from app.models.schemas import RAGSearchResult


def fold_el(s: str) -> str:
    """Lowercase Greek/Latin text and strip combining diacritics for matching."""
    if not s:
        return ""
    nfd = unicodedata.normalize("NFD", s)
    stripped = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return stripped.lower()


def _resolve_variants(label: str, table: dict[str, frozenset[str]]) -> frozenset[str]:
    fc = fold_el(label)
    if not fc:
        return frozenset()
    if fc in table:
        return table[fc] | {fc}
    for canonical, variants in table.items():
        if fc in variants:
            return variants | {canonical}
    return frozenset({fc})


# Canonical key = fold_el(root). Values = fold_el forms + common EN catalog tokens.
_COLOR_VARIANTS: dict[str, frozenset[str]] = {
    "μαυρο": frozenset({"μαυρο", "μαυρα", "μαυρη", "μαυρες", "μαυροι", "μαυρος", "black", "blk"}),
    "λευκο": frozenset({"λευκο", "λευκα", "λευκη", "λευκες", "white", "off-white", "offwhite"}),
    "μπλε": frozenset({"μπλε", "blue", "navy", "ναυτικο", "navy blue"}),
    "κοκκινο": frozenset({"κοκκινο", "κοκκινα", "κοκκινη", "κοκκινες", "red"}),
    "πρασινο": frozenset({"πρασινο", "πρασινα", "πρασινη", "green", "khaki", "χακι"}),
    "ροζ": frozenset({"ροζ", "pink"}),
    "μωβ": frozenset({"μωβ", "μoβ", "purple", "βιολετα", "βιολετί"}),
    "κιτρινο": frozenset({"κιτρινο", "κιτρινα", "yellow", "μουσταρδι", "mustard"}),
    "πορτοκαλι": frozenset({"πορτοκαλι", "πορτοκαλί", "orange"}),
    "μπεζ": frozenset({"μπεζ", "beige", "camel", "camel color"}),
    "καφε": frozenset({"καφε", "καφέ", "brown", "ταμπα", "tan"}),
    "γκρι": frozenset({"γκρι", "γρι", "grey", "gray"}),
    "χρυσο": frozenset({"χρυσο", "χρυσα", "χρυση", "gold", "golden"}),
    "ασημι": frozenset({"ασημι", "ασημένιο", "silver"}),
    "nude": frozenset({"nude", "nude tone", "beige-nude"}),
}

_MATERIAL_VARIANTS: dict[str, frozenset[str]] = {
    "δερμα": frozenset({"δερμα", "δερματινο", "δερμάτινο", "leather", "cuir"}),
    "σατεν": frozenset({"σατεν", "σατέν", "satin"}),
    "λινο": frozenset({"λινο", "λινό", "linen"}),
    "βαμβακι": frozenset({"βαμβακι", "βαμβακερό", "cotton"}),
    "βελουδο": frozenset({"βελουδο", "βελούδινο", "velvet"}),
    "μεταξι": frozenset({"μεταξι", "μεταξωτο", "silk"}),
    "πολυεστερ": frozenset({"πολυεστερ", "polyester"}),
}

_OCCASION_VARIANTS: dict[str, frozenset[str]] = {
    "γαμο": frozenset({"γαμο", "γάμος", "γαμου", "wedding", "νυφικο"}),
    "δουλεια": frozenset({"δουλεια", "δουλειά", "γραφειο", "office", "work"}),
    "βραδυ": frozenset({"βραδυ", "βραδι", "βραδινη", "evening", "night out", "εξοδο"}),
    "διακοπες": frozenset({"διακοπες", "vacation", "resort", "παραλια"}),
}

_SEASON_VARIANTS: dict[str, frozenset[str]] = {
    "καλοκαιρι": frozenset({"καλοκαιρι", "καλοκαιρινο", "summer"}),
    "χειμωνας": frozenset({"χειμωνας", "χειμερινο", "winter"}),
    "φθινοπωρο": frozenset({"φθινοπωρο", "φθινοπωρινο", "autumn", "fall"}),
    "ανοιξη": frozenset({"ανοιξη", "ανοιξιατικο", "spring"}),
}

_FIT_VARIANTS: dict[str, frozenset[str]] = {
    "oversize": frozenset({"oversize", "over-size", "φαρδυ", "φαρδια"}),
    "στενο": frozenset({"στενο", "στενη", "slim", "στενό", "fitted"}),
    "midi": frozenset({"midi", "μιντι"}),
    "maxi": frozenset({"maxi", "μαξι"}),
    "mini": frozenset({"mini", "μινι"}),
}


def haystack_matches_color(haystack: str, color_label: str) -> bool:
    if not color_label:
        return True
    h = fold_el(haystack)
    for v in _resolve_variants(color_label, _COLOR_VARIANTS):
        if v and v in h:
            return True
    return False


def haystack_matches_material(haystack: str, material_label: str) -> bool:
    if not material_label:
        return True
    h = fold_el(haystack)
    for v in _resolve_variants(material_label, _MATERIAL_VARIANTS):
        if v and v in h:
            return True
    return False


def haystack_matches_occasion(haystack: str, occasion_label: str) -> bool:
    if not occasion_label:
        return True
    h = fold_el(haystack)
    for v in _resolve_variants(occasion_label, _OCCASION_VARIANTS):
        if v and v in h:
            return True
    return fold_el(occasion_label) in h


def haystack_matches_season(haystack: str, season_label: str) -> bool:
    if not season_label:
        return True
    h = fold_el(haystack)
    for v in _resolve_variants(season_label, _SEASON_VARIANTS):
        if v and v in h:
            return True
    return fold_el(season_label) in h


def haystack_matches_fit(haystack: str, fit_label: str) -> bool:
    if not fit_label:
        return True
    h = fold_el(haystack)
    for v in _resolve_variants(fit_label, _FIT_VARIANTS):
        if v and v in h:
            return True
    return fold_el(fit_label) in h


def haystack_matches_style(haystack: str, style_label: str) -> bool:
    """Style is often soft; still use fold + substring for optional hard filter."""
    if not style_label:
        return True
    return fold_el(style_label) in fold_el(haystack)


def category_matches_filter(filter_category: str, chunk_categories: Iterable[str]) -> bool:
    """More tolerant than raw substring: fold diacritics, allow partial overlap."""
    if not filter_category:
        return True
    cf = fold_el(filter_category)
    if not cf:
        return True
    min_len = 4
    for raw in chunk_categories:
        cl = fold_el(raw)
        if not cl:
            continue
        if cf in cl or (len(cl) >= min_len and cl in cf):
            return True
    return False


def enrich_search_query(base: str, intent_like: dict[str, object]) -> str:
    """Append missing tokens so BM25/dense see explicit constraints."""
    tokens: list[str] = []
    for key in ("colors", "materials", "occasion", "season", "fit", "target_category"):
        val = intent_like.get(key)
        if isinstance(val, str) and val.strip():
            tokens.append(val.strip())
        elif isinstance(val, list):
            for x in val:
                if isinstance(x, str) and x.strip():
                    tokens.append(x.strip())
    for kw in intent_like.get("style_keywords", []) or []:
        if isinstance(kw, str) and kw.strip():
            tokens.append(kw.strip())

    base_fold = fold_el(base)
    extra: list[str] = []
    for t in tokens:
        tf = fold_el(t)
        if tf and tf not in base_fold:
            extra.append(t)
    # De-dup extra while preserving order
    seen_extra: set[str] = set()
    deduped: list[str] = []
    for t in extra:
        k = fold_el(t)
        if k not in seen_extra:
            seen_extra.add(k)
            deduped.append(t)
    if not deduped:
        return base.strip()
    return (base.strip() + " " + " ".join(deduped)).strip()


# Display lemmas (tonos) for deterministic extraction — aligned with intent parser examples.
_COLOR_DISPLAY_EL: dict[str, str] = {
    "μαυρο": "μαύρο",
    "λευκο": "λευκό",
    "μπλε": "μπλε",
    "κοκκινο": "κόκκινο",
    "πρασινο": "πράσινο",
    "ροζ": "ροζ",
    "μωβ": "μωβ",
    "κιτρινο": "κίτρινο",
    "πορτοκαλι": "πορτοκαλί",
    "μπεζ": "μπεζ",
    "καφε": "καφέ",
    "γκρι": "γκρι",
    "χρυσο": "χρυσό",
    "ασημι": "ασημί",
    "nude": "nude",
}

_MATERIAL_DISPLAY_EL: dict[str, str] = {
    "δερμα": "δερμάτινο",
    "σατεν": "σατέν",
    "λινο": "λινό",
    "βαμβακι": "βαμβακερό",
    "βελουδο": "βελούδινο",
    "μεταξι": "μεταξωτό",
    "πολυεστερ": "πολυεστέρ",
}


def _folded_contains_variant(hay_fold: str, variant_fold: str) -> bool:
    """Avoid short-token false positives (e.g. 'red' in 'reduced') when possible."""
    if not variant_fold or len(variant_fold) < 2:
        return False
    if len(variant_fold) <= 3:
        padded = f" {hay_fold} "
        return f" {variant_fold} " in padded
    return variant_fold in hay_fold


def extract_color_lemmas_from_text(text: str) -> list[str]:
    """Return Greek display lemmas for colors found in *text* (order: table order, deduped)."""
    if not (text or "").strip():
        return []
    hay = fold_el(text)
    found: list[str] = []
    seen_fold: set[str] = set()
    for canonical, variants in _COLOR_VARIANTS.items():
        display = _COLOR_DISPLAY_EL.get(canonical, canonical)
        df = fold_el(display)
        matched = False
        for v in variants | {canonical}:
            vf = fold_el(v)
            if not vf:
                continue
            if _folded_contains_variant(hay, vf):
                matched = True
                break
        if matched and df not in seen_fold:
            seen_fold.add(df)
            found.append(display)
    return found


def extract_material_lemmas_from_text(text: str) -> list[str]:
    """Return material lemmas for materials found in *text* (deduped)."""
    if not (text or "").strip():
        return []
    hay = fold_el(text)
    found: list[str] = []
    seen_fold: set[str] = set()
    for canonical, variants in _MATERIAL_VARIANTS.items():
        display = _MATERIAL_DISPLAY_EL.get(canonical, canonical)
        df = fold_el(display)
        matched = False
        for v in variants | {canonical}:
            vf = fold_el(v)
            if not vf or len(vf) < 3:
                continue
            if _folded_contains_variant(hay, vf):
                matched = True
                break
        if matched and df not in seen_fold:
            seen_fold.add(df)
            found.append(display)
    return found


def prune_by_excluded_keywords(
    results: list[RAGSearchResult],
    excluded: list[str],
) -> list[RAGSearchResult]:
    """Drop candidates whose text clearly contains a negated concept."""
    if not excluded:
        return results
    banned = [fold_el(x) for x in excluded if x and fold_el(x)]
    if not banned:
        return results
    kept: list[RAGSearchResult] = []
    for r in results:
        hay = fold_el(
            " ".join(
                [
                    r.name or "",
                    r.chunk_text or "",
                    str((r.metadata or {}).get("categories", [])),
                ]
            )
        )
        if any(b in hay for b in banned):
            continue
        kept.append(r)
    return kept
