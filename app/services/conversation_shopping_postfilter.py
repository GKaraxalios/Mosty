"""Post-retrieval filters for conversational shopping: exclude already-shown SKUs when appropriate."""
from __future__ import annotations

from app.core.logging import get_logger
from app.models.schemas import RAGSearchResult

logger = get_logger(__name__)

_MIN_PREFERRED = 2


def exclude_already_shown_skus(
    results: list[RAGSearchResult],
    shown_skus: frozenset[str],
    *,
    active: bool,
) -> tuple[list[RAGSearchResult], bool]:
    """Drop SKUs already proposed in this session when *active*.

    Returns (possibly shorter list, relaxed_duplicates).
    If too few unseen items exist, prepend unseen then backfill from the ranked list
    (may include repeats) and set relaxed_duplicates True.
    """
    if not active or not shown_skus or not results:
        return list(results), False

    unseen = [r for r in results if r.sku not in shown_skus]
    if len(unseen) >= _MIN_PREFERRED:
        logger.info(
            "conversation_shopping_postfilter: kept %d unseen (dropped %d shown)",
            len(unseen),
            len(results) - len(unseen),
        )
        return unseen, False

    if not unseen:
        logger.warning(
            "conversation_shopping_postfilter: no unseen SKUs in ranked list; keeping ranked results (relaxed)",
        )
        return list(results), True

    # Some unseen but fewer than _MIN_PREFERRED: prepend unseen, then fill from full ranking
    seen: set[str] = {r.sku for r in unseen}
    merged = list(unseen)
    for r in results:
        if r.sku in seen:
            continue
        merged.append(r)
        seen.add(r.sku)
        if len(merged) >= _MIN_PREFERRED:
            break
    had_repeat = any(r.sku in shown_skus for r in merged)
    if had_repeat:
        logger.info(
            "conversation_shopping_postfilter: backfilled from shown SKUs (limited catalog diversity)",
        )
    return merged, had_repeat
