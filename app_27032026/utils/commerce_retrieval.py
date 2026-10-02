"""Deterministic product retrieval helpers: merge structured intent, post-filter, rank."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.models.schemas import HybridSearchFilters, RAGSearchResult
from app.utils.color_taxonomy import (
    chunk_matches_color,
    extract_color_from_query_text,
    normalize_greek_commerce_query,
    primary_color_alias_for_filter,
    resolve_color_canonical,
)
from app.utils.intent import FashionIntent
from app.utils.occasion_rules import occasion_style_keywords

# Occasion keys where HybridSearchFilters.occasion substring matching is too aggressive:
# e.g. "βραδιν" rarely appears in product titles/descriptions, so hard-filtering drops all dresses.
# Ranking still uses occasion_style_keywords from parse_intent.
_OCCASION_KEYS_SKIP_METADATA_FILTER = frozenset({"evening_event", "first_date"})


def relaxed_filters_drop_color_only(f: HybridSearchFilters) -> HybridSearchFilters:
    """Widen hybrid eligible set: keep category, price, brand, etc.; drop only color metadata.

    Used when category+color metadata pre-filter yields too few chunks but the same
    color constraint is still applied in post_filter + finalize_commerce_retrieval.
    """
    n = f.model_copy()
    n.color = None
    n.color_canonical = None
    n.color_strict = False
    return n


def relaxed_filters_drop_soft_attributes(f: HybridSearchFilters) -> HybridSearchFilters:
    """Level-2 fallback: keep category, price, sale/new flags; drop color/size/style/etc.

    Soft attributes are often missing or mismatched in RAG text vs user language.
    """
    n = f.model_copy()
    n.color = None
    n.color_canonical = None
    n.color_strict = False
    n.size = None
    n.occasion = None
    n.style = None
    n.material = None
    n.season = None
    n.fit = None
    n.product_type = None
    n.brand = None
    return n


def semantic_filters_session_budget(session) -> HybridSearchFilters:
    """Level-3: query-driven search with optional session budget only (no intent metadata)."""
    f = HybridSearchFilters()
    prefs = session.user_preferences
    if prefs.budget_min is not None:
        f.min_price = float(prefs.budget_min)
    if prefs.budget_max is not None:
        f.max_price = float(prefs.budget_max)
    return f


def merge_intent_into_filters(
    base: HybridSearchFilters,
    intent: FashionIntent,
) -> HybridSearchFilters:
    """Apply structured intent fields onto request filters (copy)."""
    f = base.model_copy()
    if intent.target_category and not f.category:
        f.category = intent.target_category
    if intent.price_max is not None and f.max_price is None:
        f.max_price = float(intent.price_max)
    if intent.price_min is not None and f.min_price is None:
        f.min_price = float(intent.price_min)
    # Color: prefer canonical resolution
    raw_color = intent.color_canonical or intent.color
    if raw_color:
        cid = resolve_color_canonical(str(raw_color))
        if cid:
            f.color_canonical = cid
            f.color = primary_color_alias_for_filter(cid)
        else:
            f.color = str(raw_color).strip()
    if intent.occasion_key:
        from app.utils.occasion_rules import occasion_haystack_keyword

        kw = occasion_haystack_keyword(intent.occasion_key)
        if (
            kw
            and not f.occasion
            and intent.occasion_key not in _OCCASION_KEYS_SKIP_METADATA_FILTER
        ):
            f.occasion = kw
    if intent.formality_hint and not f.style:
        f.style = intent.formality_hint
    if intent.modesty_hint:
        # modesty as style substring for BM25/haystack
        if not f.style:
            f.style = intent.modesty_hint
    return f


def _haystack(r: RAGSearchResult) -> str:
    meta = r.metadata or {}
    cats = meta.get("categories") or []
    if isinstance(cats, str):
        cats = [cats]
    img = str(meta.get("image_url") or meta.get("image") or "")
    parts = [r.name or "", r.chunk_text or "", " ".join(cats), img]
    return " ".join(parts).lower()


def filter_results_by_target_category(
    results: list[RAGSearchResult],
    target_category: str | None,
) -> list[RAGSearchResult]:
    """Keep only rows that match the store category filter (same logic as hybrid metadata filter)."""
    if not results or not (target_category or "").strip():
        return results
    from app.services.hybrid_search_service import _category_filter_matches_chunk

    cat_lower = target_category.strip().lower()
    out: list[RAGSearchResult] = []
    for r in results:
        meta = r.metadata or {}
        cats = meta.get("categories") or []
        if isinstance(cats, str):
            cats = [cats]
        hay = _haystack(r)
        if _category_filter_matches_chunk(cat_lower, cats, hay):
            out.append(r)
    return out


def _parse_metadata_price_eur(meta: dict) -> float | None:
    """Best-effort numeric price from chunk metadata (EUR). Returns None if unknown."""
    p = meta.get("price")
    if p is None:
        return None
    if isinstance(p, bool):
        return None
    if isinstance(p, (int, float)):
        return float(p)
    s = (
        str(p)
        .strip()
        .replace("€", "")
        .replace("EUR", "")
        .replace("eur", "")
    )
    s = s.replace(",", ".")
    m = re.search(r"\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def post_filter_by_price(
    results: list[RAGSearchResult],
    *,
    min_price: float | None = None,
    max_price: float | None = None,
) -> list[RAGSearchResult]:
    """Drop products with a known numeric price outside [min_price, max_price].

    Items without a parseable price are kept (search layer may already have filtered).
    """
    if not results or (min_price is None and max_price is None):
        return results
    out: list[RAGSearchResult] = []
    for r in results:
        val = _parse_metadata_price_eur(r.metadata or {})
        if val is None:
            out.append(r)
            continue
        if min_price is not None and val < min_price:
            continue
        if max_price is not None and val > max_price:
            continue
        out.append(r)
    return out


def post_filter_by_color(
    results: list[RAGSearchResult],
    color_canonical: str | None,
    *,
    color_strict: bool = True,
) -> list[RAGSearchResult]:
    """Drop chunks that do not match canonical color when set."""
    if not color_canonical or not results:
        return results
    out = [r for r in results if chunk_matches_color(_haystack(r), color_canonical, strict=color_strict)]
    return out


def rank_commerce_results(results: list[RAGSearchResult], intent: FashionIntent) -> list[RAGSearchResult]:
    """Deterministic re-ranking: base score + occasion/style keyword overlap."""

    def bonus(hay: str) -> float:
        b = 0.0
        for kw in intent.style_keywords or []:
            if kw and str(kw).lower() in hay:
                b += 0.04
        # Soft signal only — must not dominate category or imply exact fabric match.
        for kw in occasion_style_keywords(intent.occasion_key):
            if kw.lower() in hay:
                b += 0.035
        return b

    scored = [(r.score + bonus(_haystack(r)), r) for r in results]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored]


def resolve_effective_color_canonical(intent: FashionIntent, query: str) -> str | None:
    """Final canonical color: intent field, else extract from raw query."""
    for raw in (intent.color_canonical, intent.color):
        cid = resolve_color_canonical(raw)
        if cid:
            return cid
    nq = normalize_greek_commerce_query(query or "")
    return extract_color_from_query_text(nq) or extract_color_from_query_text(query or "")


@dataclass(frozen=True)
class CommerceRetrievalOutcome:
    """Post-retrieval truth layer: separates exact color matches vs catalog alternatives."""

    results: list[RAGSearchResult]
    match_tier: Literal["exact", "alternatives", "none"]
    color_canonical: str | None


def apply_session_color_carryover(
    intent: FashionIntent,
    *,
    last_user_query: str | None,
    current_query: str,
) -> FashionIntent:
    """If a short follow-up omits color but the previous user turn named a color, keep that color.

    Avoids a second retrieval path that drops color metadata and suddenly surfaces many
    items that should have been in the first pool (fixes inconsistent follow-ups).
    """
    if not (last_user_query or "").strip():
        return intent
    eff_now = resolve_effective_color_canonical(intent, current_query or "")
    if eff_now:
        return intent
    eff_prev = resolve_effective_color_canonical(intent, last_user_query)
    if not eff_prev:
        return intent
    from dataclasses import replace

    return replace(
        intent,
        color_canonical=eff_prev,
        color=eff_prev,
    )


def finalize_commerce_retrieval(
    intent: FashionIntent,
    user_query: str,
    raw_results: list[RAGSearchResult],
) -> CommerceRetrievalOutcome:
    """When the user asked for a color, keep only chunks that match that color if any exist.

    If hybrid/fallback returned mixed colors because filters were relaxed, this layer:
    - returns only color-matching SKUs when at least one exists;
    - otherwise labels the tier as ``alternatives`` so the UI/prompt can say so honestly.
    """
    eff = resolve_effective_color_canonical(intent, user_query or "")
    if not eff:
        return CommerceRetrievalOutcome(raw_results, "none", None)

    exact = [r for r in raw_results if chunk_matches_color(_haystack(r), eff, strict=True)]
    if exact:
        return CommerceRetrievalOutcome(exact, "exact", eff)
    if raw_results:
        return CommerceRetrievalOutcome(raw_results, "alternatives", eff)
    return CommerceRetrievalOutcome([], "none", eff)
