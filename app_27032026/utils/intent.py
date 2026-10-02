"""Fashion intent extraction for the ELIZA AI AGENT Stylist.

Parses user queries into structured shopping intent: what they want to buy,
what product they're referencing, what category to search, and what style
to match. Supports product intents (trigger RAG) vs store/support intents
(info-only, no product retrieval).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace

from app.core.logging import get_logger

logger = get_logger(__name__)


def _parse_float(v: object) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

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
    style_keywords: list[str] = field(default_factory=list)
    # Structured commerce fields (LLM JSON); drive hard filters + ranking
    color: str | None = None
    color_canonical: str | None = None
    occasion_key: str | None = None
    strict_constraints: list[str] = field(default_factory=list)
    price_min: float | None = None
    price_max: float | None = None
    modesty_hint: str | None = None
    formality_hint: str | None = None


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
        '  "target_category": "Greek category from the list, or null for outfit/general/support",\n'
        '  "search_query": "clean search keywords for retrieval (or short summary for support intents)",\n'
        '  "style_keywords": ["casual", "elegant", ...],\n'
        '  "color": "canonical color id in English if clear: red|white|blue|black|pink|... or null",\n'
        '  "color_canonical": "same as color or null",\n'
        '  "occasion_key": "wedding|summer_wedding|evening_event|first_date|modest|easter_resurrection|gift|null",\n'
        '  "strict_constraints": ["category","color","occasion","price_max"] — which filters must not be dropped silently,\n'
        '  "price_min": number or null,\n'
        '  "price_max": number or null,\n'
        '  "modesty_hint": "short token or null",\n'
        '  "formality_hint": "casual|smart|formal or null"\n'
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
        "- target_category: MUST be one of the store categories below ONLY for product intents.\n"
        "  For 'complementary': this is the NEW item category, not the reference product's.\n"
        "- When the user names ONE product type (φόρεμα, παπούτσια, τσάντα, φούστα, σακάκι, …), "
        "set strict_constraints to include \"category\" so retrieval cannot drop it for unrelated items.\n"
        "- search_query: for product intents = clean search keywords. For support intents = short summary.\n"
        "- style_keywords: ONLY words the user clearly implied (e.g. σατέν, bodycon, casual). "
        "Do NOT add glam, satin, sexy, statement for generic occasion phrases — "
        "put those only via occasion_key, not as micro-requirements.\n"
        "- color / color_canonical: set when user names a color (κόκκινο→red, λευκό→white). "
        "Never confuse white with pink or red with blue.\n"
        "- occasion_key: map Greek/English (γάμος→wedding, Ανάσταση→easter_resurrection, σεμνά→modest).\n"
        "- strict_constraints: list which dimensions are non-negotiable (e.g. color+category for 'κόκκινο φόρεμα').\n"
        "- price_min / price_max when user gives budget in EUR.\n"
        "- FOLLOW-UP messages (e.g. «δεν έχει άλλα;», «κι άλλα», «more», «what else is on the site»): "
        "if CONVERSATION HISTORY shows the user already asked for a specific color and/or category "
        "(e.g. brown dresses), **keep** the same color_canonical, target_category, and search_query "
        "unless they clearly switch topic. Do NOT drop color/category just because the latest line is short.\n"
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


def _ensure_default_category_strict(intent: FashionIntent) -> FashionIntent:
    """When user asked for a specific store category, treat category as non-droppable in retrieval.

    Prevents semantic_no_filters / category_dropped from replacing dresses with unrelated SKUs.
    Skipped for outfit/styling flows where multiple categories are valid.
    """
    if not intent.target_category or not str(intent.target_category).strip():
        return intent
    if intent.intent_type in ("outfit", "styling"):
        return intent
    sc = list(intent.strict_constraints or [])
    if "category" not in sc:
        sc.append("category")
    return replace(intent, strict_constraints=sc)


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
        style_kw = list(data.get("style_keywords") or [])
        occ_raw = data.get("occasion_key") or data.get("occasion") or None
        if isinstance(occ_raw, str) and occ_raw.strip():
            from app.utils.occasion_rules import resolve_occasion_key

            occ_key = resolve_occasion_key(occ_raw.strip()) or occ_raw.strip().lower()
        else:
            occ_key = None

        intent = FashionIntent(
            intent_type=data.get("intent_type", "direct_search"),
            reference_product=data.get("reference_product") or None,
            target_category=data.get("target_category") or None,
            search_query=data.get("search_query") or fallback_query,
            style_keywords=style_kw,
            color=data.get("color") or data.get("color_canonical") or None,
            color_canonical=data.get("color_canonical") or data.get("color") or None,
            occasion_key=occ_key,
            strict_constraints=list(data.get("strict_constraints") or []),
            price_min=_parse_float(data.get("price_min")),
            price_max=_parse_float(data.get("price_max")),
            modesty_hint=data.get("modesty_hint") or None,
            formality_hint=data.get("formality_hint") or None,
        )
        # Normalize color to canonical id when possible
        try:
            from app.utils.color_taxonomy import resolve_color_canonical

            for fld in ("color", "color_canonical"):
                val = getattr(intent, fld, None)
                if val:
                    cid = resolve_color_canonical(str(val))
                    if cid:
                        intent.color_canonical = cid
                        intent.color = cid
                        break
            # Occasion maps to occasion_key + soft ranking hints in rank_commerce_results only.
            # Do NOT merge occasion_style_keywords into style_keywords — that leaked glam/satin into
            # the stylist prompt STYLE: line and implied the user asked for them.
        except Exception as exc:
            logger.debug("Intent post-normalize skipped: %s", exc)
        intent = _ensure_default_category_strict(intent)
        logger.info(
            "Intent extracted: type=%s ref=%r cat=%r query=%r color=%r occasion=%r strict=%s style=%s",
            intent.intent_type,
            intent.reference_product,
            intent.target_category,
            intent.search_query,
            intent.color_canonical,
            intent.occasion_key,
            intent.strict_constraints,
            intent.style_keywords,
        )
        return intent

    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("Intent parsing failed (%s), using fallback", exc)
        return FashionIntent(search_query=fallback_query)
