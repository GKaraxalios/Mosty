"""Post-rerank layer for soft style + novelty (chat ask only).

Calls existing maybe_rerank unchanged; adjusts scores using merged style state.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.models.schemas import ActiveSearchState, RAGSearchResult
from app.utils.commerce_semantics import fold_el
from app.utils.conversation_refinement import effective_penalize_last_shown_rerank
from app.utils.style_refinement import lemmas_for_style_keywords

logger = get_logger(__name__)

_STYLE_HIT_BONUS = 0.07
_STYLE_BONUS_CAP = 0.28
_NOVELTY_PENALTY = 0.18


def _haystack_folded(r: RAGSearchResult) -> str:
    parts = [r.name or "", r.chunk_text or ""]
    meta = r.metadata or {}
    parts.append(str(meta.get("meta_description", "") or ""))
    cats = meta.get("categories", [])
    if isinstance(cats, list):
        parts.extend(str(c) for c in cats)
    return fold_el(" ".join(parts))


def rerank_chat_retrieval_results(
    results: list[RAGSearchResult],
    *,
    effective_query: str,
    merged_state: ActiveSearchState,
    user_query: str,
    intent_novelty_request: bool | None = None,
    intent_refinement_request: bool | None = None,
    use_case: str | None = None,
    desired_output: str | None = None,
    brand: str | None = None,
    brand_mode: str | None = None,
) -> list[RAGSearchResult]:
    """Re-order after heuristic rerank using accumulated style keywords + novelty."""
    if not results:
        return results

    lemmas = lemmas_for_style_keywords(list(merged_state.style_keywords or []))
    # Penalize repeating last turn's SKUs when novelty/refinement (LLM or heuristics) and
    # not narrowing with "from these options" (candidate restriction).
    want_novelty = effective_penalize_last_shown_rerank(
        user_query,
        intent_novelty_request,
        intent_refinement_request,
    ) and not merged_state.restrict_to_candidates
    last_shown = set(merged_state.last_result_skus or [])

    if not lemmas and not (want_novelty and last_shown):
        return results

    scored: list[tuple[RAGSearchResult, float]] = []
    for r in results:
        base = r.rerank_score if r.rerank_score is not None else r.score
        delta = 0.0
        meta = r.metadata or {}
        cats = meta.get("categories", []) or []
        cats_l = [str(c).lower() for c in cats] if isinstance(cats, list) else []
        product_type = str(meta.get("product_type") or "").lower()
        brand_meta = str(meta.get("brand") or "").lower()
        reason_signals = meta.get("reason_signals") or []
        if not isinstance(reason_signals, list):
            reason_signals = []

        if lemmas:
            h = _haystack_folded(r)
            hits = 0
            for lem in lemmas:
                if len(lem) >= 3 and lem in h:
                    hits += 1
            if hits:
                delta += min(_STYLE_BONUS_CAP, _STYLE_HIT_BONUS * hits)
        if want_novelty and last_shown and r.sku in last_shown:
            delta -= _NOVELTY_PENALTY

        # ── Use-case aware tweaks (daily wear, outfits, etc.) ─────────────
        if use_case == "daily_wear":
            # Boost versatile everyday staples
            if any(
                k in product_type
                for k in [
                    "t-shirt",
                    "tshirt",
                    "shirt",
                    "knit",
                    "sweater",
                    "jean",
                    "trouser",
                    "pant",
                    "chino",
                    "sneaker",
                    "flat",
                    "loafer",
                    "everyday dress",
                    "day dress",
                ]
            ):
                delta += 0.08
            # Penalize clearly evening/party pieces
            if any(k in product_type for k in ["evening", "party", "gown"]):
                delta -= 0.15
                reason_signals.append({"type": "occasion_mismatch_evening", "strength": "medium", "value": product_type})

        # Accessories penalty when user wants outfits / daily wear
        is_accessory_cat = any(
            ac in cats_l for ac in ["accessories", "scarves", "hats", "jewellery", "belts"]
        )
        if desired_output == "outfit" and use_case == "daily_wear":
            if is_accessory_cat:
                delta -= 0.1
                reason_signals.append({"type": "accessory_penalty", "strength": "low"})

        # Outfit anchors: bottoms / dresses that structure a look
        if desired_output == "outfit":
            if any(core in product_type for core in ["jean", "trouser", "pant", "skirt", "dress"]):
                delta += 0.06
                reason_signals.append({"type": "outfit_anchor_bonus", "strength": "medium", "value": product_type})

        # Brand-aware adjustments: boost same-brand products heavily when brand intent is explicit.
        if brand:
            bf = fold_el(brand)
            if bf and brand_meta:
                same_brand = bf in fold_el(brand_meta)
                if same_brand:
                    delta += 0.25
                    reason_signals.append({"type": "brand_exact_match", "strength": "high", "value": brand_meta})
                elif brand_mode == "exact":
                    delta -= 0.15
                    reason_signals.append({"type": "brand_mismatch_penalty", "strength": "medium", "value": brand_meta})

        # Attach reason_signals back to metadata for downstream reasoning
        new_meta = dict(meta)
        existing_rs = new_meta.get("reason_signals") or []
        if isinstance(existing_rs, list):
            new_meta["reason_signals"] = existing_rs + reason_signals
        else:
            new_meta["reason_signals"] = reason_signals

        new_score = base + delta
        r_copy = r.model_copy(update={"rerank_score": round(new_score, 4), "metadata": new_meta})
        scored.append((r_copy, new_score))

    scored.sort(key=lambda x: x[1], reverse=True)
    out = [x[0] for x in scored]
    if lemmas or (want_novelty and last_shown):
        logger.info(
            "soft_style_rerank: lemmas=%d novelty=%s penalized_last=%s",
            len(lemmas),
            want_novelty,
            sum(1 for x in out if x.sku in last_shown),
        )
    return out
