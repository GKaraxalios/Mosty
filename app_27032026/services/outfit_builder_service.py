"""Outfit Builder: hybrid search per slot + compatibility heuristics + suggestions."""
from __future__ import annotations

import random
import re
from typing import Any

from app.core.logging import get_logger
from app.models.schemas import (
    HybridSearchFilters,
    OutfitBuilderContext,
    OutfitProductRef,
    OutfitSlotsState,
    OutfitSuggestionItem,
    RAGSearchResult,
)
from app.services.hybrid_search_service import HybridSearchService

logger = get_logger(__name__)

SLOT_ORDER = ("top", "bottom", "shoes", "outerwear", "accessory")

# Broad retrieval queries (multilingual hints for the hybrid embedder + BM25)
_SLOT_QUERIES: dict[str, str] = {
    "top": "μπλούζα πουκάμισο μπλουζάκι top shirt blouse sweater t-shirt",
    "bottom": "παντελόνι τζιν φούστα σορτς pants jeans trousers skirt shorts",
    "shoes": "παπούτσια μπότες sneakers shoes boots heels loafers",
    "outerwear": "σακάκι παλτό μπουφάν jacket coat blazer cardigan",
    "accessory": "τσάντα ζώνη κασκόλ τσάντες bag belt scarf hat jewelry",
}

def _result_to_ref(r: RAGSearchResult) -> OutfitProductRef:
    meta = r.metadata or {}
    img = meta.get("image_url")
    price = meta.get("price")
    try:
        p = float(price) if price is not None else None
    except (TypeError, ValueError):
        p = None
    return OutfitProductRef(
        sku=r.sku,
        name=r.name,
        url=r.url,
        image=str(img) if img else None,
        price=p,
        currency="EUR",
        score=r.score,
    )


def _context_filters(ctx: OutfitBuilderContext) -> HybridSearchFilters:
    return HybridSearchFilters(
        occasion=ctx.occasion,
        style=ctx.style_family,
        season=ctx.season,
        language=ctx.language,
    )


def _merge_exclude(eligible: list[RAGSearchResult], exclude: set[str]) -> list[RAGSearchResult]:
    return [r for r in eligible if r.sku not in exclude]


class OutfitBuilderService:
    def __init__(self) -> None:
        self._search = HybridSearchService()

    def build_query(self, slot: str, ctx: OutfitBuilderContext, override: str | None) -> str:
        if override and override.strip():
            return override.strip()
        base = _SLOT_QUERIES.get(slot, "fashion clothing")
        bits = [base]
        if ctx.occasion:
            bits.append(ctx.occasion)
        if ctx.style_family:
            bits.append(ctx.style_family)
        if ctx.season:
            bits.append(ctx.season)
        return " ".join(bits)

    async def search_slot(
        self,
        slot: str,
        ctx: OutfitBuilderContext,
        *,
        query_override: str | None = None,
        exclude_skus: list[str] | None = None,
        top_k: int = 12,
    ) -> list[OutfitProductRef]:
        if slot not in _SLOT_QUERIES:
            return []
        fl = _context_filters(ctx)
        q = self.build_query(slot, ctx, query_override)
        try:
            raw = await self._search.search(query=q, top_k=max(top_k * 2, 16), filters=fl)
        except Exception as exc:
            logger.warning("outfit slot search failed slot=%s: %s", slot, exc)
            return []
        ex = set(exclude_skus or [])
        raw = _merge_exclude(raw, ex)[:top_k]
        return [_result_to_ref(r) for r in raw]

    def _slots_dict(self, slots: OutfitSlotsState) -> dict[str, OutfitProductRef | None]:
        return {
            "top": slots.top,
            "bottom": slots.bottom,
            "shoes": slots.shoes,
            "outerwear": slots.outerwear,
            "accessory": slots.accessory,
        }

    def _mismatch_warnings(self, slots: OutfitSlotsState) -> list[str]:
        d = self._slots_dict(slots)
        names = [p.name.lower() for p in d.values() if p]
        if len(names) < 2:
            return []
        warnings: list[str] = []
        pattern_kw = ("stripe", "ρίγα", "print", "φλοράλ", "floral", "animal", "λεοπάρ")
        hits = sum(1 for n in names for k in pattern_kw if k in n)
        if hits >= 2:
            warnings.append(
                "Πολλά prints μαζί — δοκίμασε ουδέτερο κομμάτι για πιο ισορροπημένο look.",
            )
        formal_kw = ("σακάκι", "blazer", "κοστούμι", "suit")
        casual_kw = ("σορτς", "shorts", "φόρμα", "hoodie")
        has_formal = any(any(k in n for k in formal_kw) for n in names)
        has_casual = any(any(k in n for k in casual_kw) for n in names)
        if has_formal and has_casual:
            warnings.append(
                "Μίξη πολύ επίσημου και πολύ casual — ίσως χρειάζεται πιο συνεπές στυλ.",
            )
        return warnings

    def _simple_compatibility(self, slots: OutfitSlotsState) -> float | None:
        d = self._slots_dict(slots)
        filled = [p for p in d.values() if p]
        if len(filled) < 2:
            return None
        scores = [p.score for p in filled if p.score is not None]
        if not scores:
            return 0.75
        return round(max(0.0, min(1.0, sum(scores) / len(scores))), 3)

    async def build_suggestions(
        self,
        slots: OutfitSlotsState,
        ctx: OutfitBuilderContext,
    ) -> tuple[list[OutfitSuggestionItem], list[str], float | None]:
        d = self._slots_dict(slots)
        warnings = self._mismatch_warnings(slots)
        score = self._simple_compatibility(slots)
        suggestions: list[OutfitSuggestionItem] = []
        chosen = {p.sku for p in d.values() if p}

        for slot in SLOT_ORDER:
            if d.get(slot):
                continue
            found = await self.search_slot(slot, ctx, exclude_skus=list(chosen), top_k=5)
            if not found:
                continue
            top = found[0]
            suggestions.append(
                OutfitSuggestionItem(
                    type="complete_look",
                    message=f"Πρόταση για {slot}: {top.name}",
                    slot=slot,
                    product=top,
                ),
            )
            if len(suggestions) >= 4:
                break

        alts = await self._alternatives(d, ctx, chosen)
        suggestions.extend(alts)
        return suggestions, warnings, score

    async def _alternatives(
        self,
        d: dict[str, OutfitProductRef | None],
        ctx: OutfitBuilderContext,
        chosen: set[str],
    ) -> list[OutfitSuggestionItem]:
        out: list[OutfitSuggestionItem] = []
        for slot, p in d.items():
            if not p:
                continue
            more = await self.search_slot(
                slot,
                ctx,
                exclude_skus=list(chosen | {p.sku}),
                top_k=3,
            )
            for m in more[:1]:
                out.append(
                    OutfitSuggestionItem(
                        type="alternative",
                        message=f"Εναλλακτικό για {slot}",
                        slot=slot,
                        product=m,
                    ),
                )
            if len(out) >= 3:
                break
        return out

    async def randomize_outfit(
        self,
        ctx: OutfitBuilderContext,
    ) -> OutfitSlotsState:
        """Pick one random product per slot from top search results."""
        state = OutfitSlotsState()
        chosen: set[str] = set()
        for slot in SLOT_ORDER:
            batch = await self.search_slot(slot, ctx, exclude_skus=list(chosen), top_k=8)
            if not batch:
                continue
            pick = random.choice(batch[: min(5, len(batch))])
            chosen.add(pick.sku)
            setattr(state, slot, pick)
        return state


def slots_to_payload(slots: OutfitSlotsState) -> dict[str, Any]:
    d: dict[str, Any] = {}
    for k in SLOT_ORDER:
        p = getattr(slots, k, None)
        if p:
            d[k] = p.model_dump()
        else:
            d[k] = None
    return d


def payload_to_slots(data: dict[str, Any]) -> OutfitSlotsState:
    st = OutfitSlotsState()
    for k in SLOT_ORDER:
        raw = data.get(k)
        if raw and isinstance(raw, dict) and raw.get("sku"):
            setattr(st, k, OutfitProductRef.model_validate(raw))
    return st
