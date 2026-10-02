"""Multi-turn conversational search state: merge, price parsing, candidate-set refinement."""
from __future__ import annotations

import re

from app.core.logging import get_logger
from app.models.schemas import ActiveSearchState, HybridSearchFilters
from app.utils.intent import FashionIntent

logger = get_logger(__name__)


def _dedupe_style_keywords(a: list[str], b: list[str]) -> list[str]:
    return list(dict.fromkeys(list(a or []) + list(b or [])))


# Phrases implying user wants to narrow to the last shown list (Greek + EN).
_RESTRICT_MARKERS_EL = (
    "από τις επιλογές",
    "από αυτά",
    "από τα παραπάνω",
    "από τα προηγούμενα",
    "μόνο από αυτά",
    "μόνο από τις επιλογές",
    "κράτα αυτά",
    "από την προηγούμενη λίστα",
    "από τα προϊόντα που",
)
_RESTRICT_MARKERS_EN = (
    "from these",
    "from those",
    "from the options",
    "from the list",
    "only from these",
    "only these",
    "among these",
)


def detect_restrict_to_candidates(query: str) -> bool:
    """True if the user likely wants to filter within the last result set."""
    q = (query or "").strip().lower()
    if not q:
        return False
    for m in _RESTRICT_MARKERS_EL:
        if m in q:
            return True
    for m in _RESTRICT_MARKERS_EN:
        if m in q:
            return True
    return False


def parse_price_range_el(text: str) -> tuple[float | None, float | None]:
    """Extract min/max EUR from Greek-ish shopping text. Returns (min, max) with None if absent."""
    if not (text or "").strip():
        return None, None
    t = text.replace("€", " ").replace(",", ".")
    t = re.sub(r"\s+", " ", t.lower())

    # Range first (avoid matching bare "έως" as "up to" in "από 90 έως 120")
    m = re.search(
        r"(?:από\s*)?(\d+(?:\.\d+)?)\s*(?:-|έως|εως|ως|ωσ|to)\s*(\d+(?:\.\d+)?)",
        t,
    )
    if m:
        return float(m.group(1)), float(m.group(2))

    m = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", t)
    if m:
        return float(m.group(1)), float(m.group(2))

    # "κάτω από 100" / "μέχρι 100" (not bare έως — conflicts with ranges)
    m = re.search(
        r"(?:κάτω από|μεχρι|μέχρι|under|below|max|up to)\s*(\d+(?:\.\d+)?)",
        t,
    )
    if m:
        return None, float(m.group(1))

    return None, None


def merge_active_search_state(
    prev: ActiveSearchState | None,
    intent: FashionIntent,
    query: str,
    has_history: bool,
    last_result_skus: list[str],
) -> ActiveSearchState:
    """Merge previous conversational state with current intent + query.

    When there is no history, builds a fresh state from intent + price parse.
    On follow-ups, keeps unspecified slots from *prev* and overrides when intent/query updates.
    """
    q = (query or "").strip()
    rp, mp = parse_price_range_el(q)

    if not has_history or prev is None:
        st = ActiveSearchState(
            category=intent.target_category,
            color=intent.colors[0] if intent.colors else None,
            material=intent.materials[0] if intent.materials else None,
            occasion=intent.occasion,
            fit=intent.fit,
            size=intent.size,
            season=intent.season,
            excluded_keywords=list(intent.excluded_keywords or []),
        )
        if intent.style_keywords:
            st.style_keywords = list(intent.style_keywords)
            st.style = intent.style_keywords[0]
        if rp is not None:
            st.min_price = rp
        if mp is not None:
            st.max_price = mp
        if detect_restrict_to_candidates(q) and last_result_skus:
            st.restrict_to_candidates = True
            st.candidate_skus = list(last_result_skus)
        return st

    out = prev.model_copy(deep=True)

    # Category: explicit intent wins
    if intent.target_category:
        out.category = intent.target_category
    if intent.colors:
        out.color = intent.colors[0]
    if intent.materials:
        out.material = intent.materials[0]
    if intent.occasion:
        out.occasion = intent.occasion
    if intent.fit:
        out.fit = intent.fit
    if intent.size:
        out.size = intent.size
    if intent.season:
        out.season = intent.season
    out.style_keywords = _dedupe_style_keywords(
        list(prev.style_keywords or []),
        list(intent.style_keywords or []),
    )
    if intent.style_keywords:
        out.style = intent.style_keywords[0]
    if intent.excluded_keywords:
        merged = list(dict.fromkeys(list(out.excluded_keywords) + list(intent.excluded_keywords)))
        out.excluded_keywords = merged

    if rp is not None:
        out.min_price = rp
    if mp is not None:
        out.max_price = mp

    # New topic: different category → drop candidate restriction
    if intent.target_category and prev.category and intent.target_category != prev.category:
        out.restrict_to_candidates = False
        out.candidate_skus = []

    if detect_restrict_to_candidates(q) and last_result_skus:
        out.restrict_to_candidates = True
        out.candidate_skus = list(last_result_skus)
    elif not detect_restrict_to_candidates(q):
        # Do not clear restrict flag on every message — only when not asking for "from options"
        pass

    return out


def apply_active_state_to_hybrid_filters(filters: HybridSearchFilters, st: ActiveSearchState) -> None:
    """Overlay merged conversational state onto HybridSearchFilters (explicit overrides)."""
    if st.category:
        filters.category = st.category
    if st.color:
        filters.color = st.color
    if st.min_price is not None:
        filters.min_price = st.min_price
    if st.max_price is not None:
        filters.max_price = st.max_price
    if st.material:
        filters.material = st.material
    if st.occasion:
        filters.occasion = st.occasion
    if st.fit:
        filters.fit = st.fit
    if st.size:
        filters.size = st.size
    if st.season:
        filters.season = st.season
    if st.style:
        filters.style = st.style


def filter_results_by_candidate_skus(
    results: list,
    st: ActiveSearchState,
) -> list:
    """Return only results whose SKU is in candidate_skus when restrict_to_candidates is set."""
    if not st.restrict_to_candidates or not st.candidate_skus:
        return results
    allow = set(st.candidate_skus)
    filtered = [r for r in results if r.sku in allow]
    if not filtered:
        logger.warning(
            "candidate_sku_filter removed all %d results; keeping full set (restrict_to_candidates)",
            len(results),
        )
        return results
    logger.info(
        "candidate_sku_filter applied: %d -> %d SKUs",
        len(results),
        len(filtered),
    )
    return filtered
