"""Fashion intent extraction for the ELIZA AI AGENT Stylist.

Parses user queries into structured shopping intent: what they want to buy,
what product they're referencing, what category to search, and what style
to match. Supports product intents (trigger RAG) vs store/support intents
(info-only, no product retrieval).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# Intents that require product retrieval and product cards. All others are info-only.
PRODUCT_INTENT_TYPES = frozenset({
    "product_search",
    "recommendation",
    "complementary",
    "outfit",
    "comparison",
    "popular_products",  # best sellers, trending, "τι αγοράζουν περισσότερο"
    # Legacy names (LLM may still return these)
    "direct_search",
    "styling",
})


def should_retrieve_products(intent_type: str) -> bool:
    """True if this intent should trigger RAG search and product recommendations."""
    return intent_type in PRODUCT_INTENT_TYPES


STORE_CATEGORIES = (
    "Φορέματα, Τοπ, Παντελόνια, Σορτς, Φούστες, Ολόσωμες Φόρμες, "
    "Πουκάμισα, Σακάκια, Ζακέτες, Πλεκτά, Παλτό, Μπουφάν, Μαγιό, "
    "Σανδάλια, Πέδιλα, Γόβες, Μποτάκια, Μπότες, Εσπαντρίγιες, Παπούτσια, "
    "Τσάντες, Τσάντες Παραλίας, Φάκελοι, Clutch, Σακίδια, "
    "Κολιέ, Βραχιόλια, Δαχτυλίδια, Σκουλαρίκια, Κοσμήματα, "
    "Γυαλιά Ηλίου, Ζώνες, Καπέλα, Αξεσουάρ Μαλλιών, Αξεσουάρ, "
    "Καλλυντικά, Πετσέτες, Beachwear, Swimwear"
)


@dataclass
class FashionIntent:
    intent_type: str = "direct_search"
    reference_product: str | None = None
    target_category: str | None = None
    search_query: str = ""
    # Brand / label context (parsed from user query or conversation)
    brand: str | None = None
    # How to treat the brand: "exact" (only this brand) or "style_like" (similar aesthetic).
    brand_mode: str | None = None
    # Soft preferences (boost via retrieval text; optional loose metadata if mapped)
    style_keywords: list[str] = field(default_factory=list)
    # Hard-ish facets (must align with catalog text when filters apply)
    colors: list[str] = field(default_factory=list)
    materials: list[str] = field(default_factory=list)
    occasion: str | None = None
    season: str | None = None
    fit: str | None = None
    size: str | None = None
    # Negations: phrases or stems to exclude from results (e.g. floral, chunky)
    excluded_keywords: list[str] = field(default_factory=list)
    # LLM tri-state: None = not specified (use heuristics); True/False = explicit
    novelty_request: bool | None = None  # user wants different products / not the same picks
    refinement_request: bool | None = None  # user refines style/budget/feel vs previous turn


def build_intent_prompt(
    query: str,
    conversation_context: str,
    lang: str,
) -> str:
    """Build the prompt that asks the LLM to extract structured fashion intent."""

    instruction = (
        "You are a fashion intent parser. Analyze the shopping query below and "
        "extract structured intent as JSON.\n"
    ) if lang != "el" else (
        "Είσαι ένας fashion intent parser. Ανάλυσε το ερώτημα αγοράς παρακάτω "
        "και εξήγαγε δομημένο intent ως JSON.\n"
    )

    schema = (
        "Output ONLY valid JSON with exactly these fields:\n"
        "{\n"
        '  "intent_type": "product_search" | "recommendation" | "complementary" | "outfit" | "comparison" | '
        '"popular_products" | '
        '"store_info" | "support_hours" | "support_stores" | "support_contact" | "support_shipping" | '
        '"support_returns" | "support_terms" | "fallback_general",\n'
        '  "reference_product": "product name the user mentions BY NAME, or null",\n'
        '  "brand": "brand or label the user asks for (e.g. Nadia Rapti), or null",\n'
        '  "brand_mode": "exact | style_like | null (exact = user clearly wants products OF this brand; style_like = user wants similar style to this brand)",\n'
        '  "target_category": "Greek category from the list, or null for outfit/general/support",\n'
        '  "search_query": "clean search keywords for retrieval (or short summary for support intents)",\n'
        '  "style_keywords": ["soft style/look words only: elegant, casual, chic, minimal, sporty, sexy, ..."],\n'
        '  "colors": ["normalized color lemmas in Greek or English, e.g. μαύρο, λευκό, navy — empty if none"],\n'
        '  "materials": ["leather, σατέν, λινό, βαμβακερό — empty if none"],\n'
        '  "occasion": "short phrase or null (γάμος, δουλειά, βράδυ, διακοπές, ...)",\n'
        '  "season": "καλοκαίρι, χειμώνας, ... or null",\n'
        '  "fit": "oversize, στενό, midi, maxi, mini or null",\n'
        '  "size": "EU size or S/M/L if user said it, else null",\n'
        '  "excluded_keywords": ["tokens to avoid in products, from negations: όχι floral → floral, όχι chunky → chunky"],\n'
        '  "novelty_request": true | false | null,\n'
        '  "refinement_request": true | false | null\n'
        "}\n"
    )

    rules = (
        "RULES:\n"
        "- intent_type — use EXACTLY one:\n"
        "  PRODUCT intents (user wants products):\n"
        "    product_search = user wants a specific product type/category\n"
        "    recommendation = user wants suggestions / style advice\n"
        "    complementary = user wants something to MATCH another product\n"
        "    outfit = user wants a full look / multiple categories\n"
        "    comparison = user wants to compare products\n"
        "    popular_products = user asks for most popular, best sellers, trending, 'τι αγοράζουν περισσότερο', 'πιο δημοφιλή'\n"
        "  SUPPORT/STORE intents (user asks about store info — do NOT retrieve products):\n"
        "    support_hours = opening hours, when the store is open\n"
        "    support_stores = store locations, showroom, addresses\n"
        "    support_contact = phone, email, how to contact\n"
        "    support_shipping = delivery, shipping, courier, tracking\n"
        "    support_returns = returns, refunds, exchange policy\n"
        "    support_terms = terms of use, legal, privacy, complaints\n"
        "    store_info = other general store info\n"
        "    fallback_general = unrelated or unclear question\n"
        "- For ANY support_* or store_info or fallback_general: set target_category to null, "
        "search_query to a short summary of the question (for logging only). No product search will be run.\n"
        "- reference_product: set ONLY if the user mentions a specific product BY NAME\n"
        "  (e.g. 'Tencel Longdress', 'Nenna rusty pink'). Do NOT set for generic categories.\n"
        "- brand: normalized brand/label if the user clearly mentions one (e.g. 'Nadia Rapti', 'Zara').\n"
        "- brand_mode:\n"
        "  exact = user wants products OF this exact brand (e.g. 'έχεις κάτι Nadia Rapti;', 'θέλω φόρεμα Zara'),\n"
        "  style_like = user wants similar style to this brand but not strictly the brand (e.g. 'σε ύφος Nadia Rapti', "
        "'στυλ σε ναντια ραπτη', 'κάτι σαν Mango αλλά όχι Mango').\n"
        "- target_category: MUST be one of the store categories below ONLY for product intents.\n"
        "  For 'complementary': this is the NEW item category, not the reference product's.\n"
        "- search_query: for product intents = clean search keywords. For support intents = short summary.\n"
        "- HARD facets (user was explicit or follow-up inherits them; put in dedicated fields, NOT only in style_keywords):\n"
        "  colors, materials, occasion, season, fit, size — use Greek lemmas where natural (μαύρο not μαύρα if lemma).\n"
        "- SOFT preferences only in style_keywords (elegant, chic, classy, modern) — do NOT duplicate pure colors here.\n"
        "- Follow-ups: read CONVERSATION HISTORY; keep prior category/colors unless user changes them "
        "(e.g. 'ίδιο αλλά μαύρο' → keep category, set colors=['μαύρο']).\n"
        "- excluded_keywords: for 'όχι X', 'χωρίς X', 'όχι αυτά' + implied dislike from context, list stems "
        "that must NOT appear in product title/description (lowercase, language of catalog if mixed).\n"
        "- novelty_request: true if the user wants DIFFERENT products than already shown (κάτι άλλο, όχι τα ίδια, "
        "more variety, alternatives). false if this is a fresh search or they are happy to see prior picks again. "
        "null only if truly unclear.\n"
        "- refinement_request: true if the user refines the SAME search (πιο minimal, πιο κομψό, μέχρι 90€, "
        "όχι τόσο βραδινό, same category/color but different feel). false if they start a new unrelated topic. "
        "null only if unclear.\n"
        "- Synonyms map to one normalized token in colors/materials (φθηνό/οικονομικό → do not put in colors; "
        "budget is not a color — leave colors empty unless a hue is stated).\n"
    )

    parts = [
        instruction,
        schema,
        rules,
        f"STORE CATEGORIES:\n{STORE_CATEGORIES}\n",
    ]

    if conversation_context:
        parts.append(f"CONVERSATION HISTORY:\n{conversation_context}\n")

    parts.append(f"QUERY: {query}\n\nJSON:")
    return "\n".join(parts)


def _opt_str(val: object) -> str | None:
    if val is None:
        return None
    if not isinstance(val, str):
        return None
    s = val.strip()
    return s or None


def _tri_state_bool(data: dict[str, Any], key: str) -> bool | None:
    """Parse optional JSON boolean; missing key → None."""
    if key not in data:
        return None
    v = data[key]
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        if v == 1:
            return True
        if v == 0:
            return False
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "yes", "1", "ναι"):
            return True
        if s in ("false", "no", "0", "οχι", "όχι"):
            return False
    return None


def parse_intent(raw: str, fallback_query: str) -> FashionIntent:
    """Parse LLM JSON output into FashionIntent. Falls back gracefully."""
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        data = json.loads(text)

        def _str_list(key: str) -> list[str]:
            raw = data.get(key) or []
            if isinstance(raw, str):
                return [raw] if raw.strip() else []
            if isinstance(raw, list):
                return [str(x).strip() for x in raw if str(x).strip()]
            return []

        intent = FashionIntent(
            intent_type=data.get("intent_type", "direct_search"),
            reference_product=data.get("reference_product") or None,
            target_category=data.get("target_category") or None,
            search_query=data.get("search_query") or fallback_query,
            brand=_opt_str(data.get("brand")),
            brand_mode=_opt_str(data.get("brand_mode")),
            style_keywords=_str_list("style_keywords"),
            colors=_str_list("colors"),
            materials=_str_list("materials"),
            occasion=_opt_str(data.get("occasion")),
            season=_opt_str(data.get("season")),
            fit=_opt_str(data.get("fit")),
            size=_opt_str(data.get("size")),
            excluded_keywords=_str_list("excluded_keywords"),
            novelty_request=_tri_state_bool(data, "novelty_request"),
            refinement_request=_tri_state_bool(data, "refinement_request"),
        )

        logger.info(
            "Intent extracted: type=%s ref=%r brand=%r brand_mode=%r cat=%r query=%r style=%s colors=%s mat=%s occ=%r excl=%s "
            "novelty=%s refinement=%s",
            intent.intent_type,
            intent.reference_product,
            intent.brand,
            intent.brand_mode,
            intent.target_category,
            intent.search_query,
            intent.style_keywords,
            intent.colors,
            intent.materials,
            intent.occasion,
            intent.excluded_keywords,
            intent.novelty_request,
            intent.refinement_request,
        )
        return intent

    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("Intent parsing failed (%s), using fallback", exc)
        return FashionIntent(search_query=fallback_query)
