from __future__ import annotations

from typing import Optional, Tuple, Dict, Any
import json
import os

from app.utils.commerce_semantics import fold_el


def _load_brand_aliases() -> Dict[str, dict]:
    """Load brand aliases from JSON path if configured, else use built-in defaults."""
    default: Dict[str, dict] = {
        "nadia_rapti": {
            "canonical": "Nadia Rapti",
            "aliases": [
                "nadia rapti",
                "nantia rapti",
                "ναντια ραπτη",
                "νάντια ράπτη",
                "ναδια ραπτη",
                "nadia ραπτη",
                "ναντια rapti",
                "nadia rapty",
            ],
        },
    }
    path = os.getenv("MOSTY_BRAND_ALIASES_PATH")
    if not path:
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data: Dict[str, Any] = json.load(f)
        out: Dict[str, dict] = {}
        for did, info in data.items():
            if not isinstance(info, dict):
                continue
            aliases = info.get("aliases") or []
            if not isinstance(aliases, list):
                continue
            out[did] = {
                "canonical": info.get("canonical") or did.replace("_", " ").title(),
                "aliases": [str(a) for a in aliases if str(a).strip()],
            }
        return out or default
    except Exception:
        return default


_BRAND_ALIASES: Dict[str, dict] = _load_brand_aliases()

_FOLDED_BRAND_INDEX: dict[str, str] = {}
for designer_id, info in _BRAND_ALIASES.items():
    for alias in info.get("aliases", []):
        key = fold_el(alias)
        if key:
            _FOLDED_BRAND_INDEX[key] = designer_id


_GREEK_TO_GREEKLISH = {
    "α": "a", "ά": "a",
    "β": "v",
    "γ": "g",
    "δ": "d",
    "ε": "e", "έ": "e",
    "ζ": "z",
    "η": "i", "ή": "i",
    "θ": "th",
    "ι": "i", "ί": "i", "ϊ": "i", "ΐ": "i",
    "κ": "k",
    "λ": "l",
    "μ": "m",
    "ν": "n",
    "ξ": "x",
    "ο": "o", "ό": "o",
    "π": "p",
    "ρ": "r",
    "σ": "s", "ς": "s",
    "τ": "t",
    "υ": "y", "ύ": "y", "ϋ": "y", "ΰ": "y",
    "φ": "f",
    "χ": "x",
    "ψ": "ps",
    "ω": "o", "ώ": "o",
}


def _to_greeklish(text: str) -> str:
    out: list[str] = []
    for ch in text:
        lower = ch.lower()
        if lower in _GREEK_TO_GREEKLISH:
            out.append(_GREEK_TO_GREEKLISH[lower])
        else:
            out.append(ch)
    return "".join(out)


def resolve_designer_from_text(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (designer_id, canonical_name) if any alias appears in text, else (None, None)."""
    if not text or not text.strip():
        return None, None
    # 1) Try on folded original text (Greek or Latin)
    hay = fold_el(text)
    if hay:
        for folded_alias, designer_id in _FOLDED_BRAND_INDEX.items():
            if folded_alias in hay:
                info = _BRAND_ALIASES.get(designer_id) or {}
                return designer_id, info.get("canonical", None)

    # 2) If not found, transliterate Greek chars to greeklish and try again
    greeklish = _to_greeklish(text)
    if greeklish and greeklish != text:
        hay2 = fold_el(greeklish)
        if hay2:
            for folded_alias, designer_id in _FOLDED_BRAND_INDEX.items():
                if folded_alias in hay2:
                    info = _BRAND_ALIASES.get(designer_id) or {}
                    return designer_id, info.get("canonical", None)
    return None, None


def normalize_brand_name(raw_brand: str) -> Tuple[Optional[str], Optional[str]]:
    """Normalize a brand string to (designer_id, canonical_name) using aliases."""
    if not raw_brand or not raw_brand.strip():
        return None, None
    bf = fold_el(raw_brand)
    if not bf:
        return None, None
    # Direct alias hit
    if bf in _FOLDED_BRAND_INDEX:
        did = _FOLDED_BRAND_INDEX[bf]
        info = _BRAND_ALIASES.get(did) or {}
        return did, info.get("canonical", None)
    # Try as substring inside an alias (e.g. "ναδια ραπτη" vs "ναντια ραπτη")
    for folded_alias, designer_id in _FOLDED_BRAND_INDEX.items():
        if bf in folded_alias or folded_alias in bf:
            info = _BRAND_ALIASES.get(designer_id) or {}
            return designer_id, info.get("canonical", None)
    return None, None


def brand_matches_haystack(brand: str, haystack: str) -> bool:
    """Check if a (possibly Greek/Latin mixed) brand appears in haystack using aliases."""
    if not brand or not haystack:
        return False
    did, _ = normalize_brand_name(brand)
    if not did:
        # Fallback to simple folded substring check
        return fold_el(brand) in fold_el(haystack)
    info = _BRAND_ALIASES.get(did) or {}
    aliases = info.get("aliases", set())
    hay_fold = fold_el(haystack)
    for alias in aliases:
        if fold_el(alias) in hay_fold:
            return True
    # Also allow canonical name
    canon = info.get("canonical")
    if canon and fold_el(canon) in hay_fold:
        return True
    return False


