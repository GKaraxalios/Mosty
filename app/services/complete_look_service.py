"""Complete-the-look engine.

Given an anchor product (SKU), recommends complementary items from other
categories to build a full outfit. Uses deterministic category-pairing rules
and falls back to generic complement categories when the anchor's category
is not mapped.
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.models.schemas import HybridSearchFilters
from app.services.hybrid_search_service import HybridSearchService
from app.services.rag_ingestion_service import get_chunk_store

logger = get_logger(__name__)

_CATEGORY_COMPLEMENTS: dict[str, list[str]] = {
    "φορέματα": ["παπούτσια", "τσάντες", "αξεσουάρ", "κοσμήματα"],
    "φόρεμα": ["παπούτσια", "τσάντες", "αξεσουάρ", "κοσμήματα"],
    "dresses": ["shoes", "bags", "accessories", "jewelry"],
    "dress": ["shoes", "bags", "accessories", "jewelry"],
    "σακάκια": ["παντελόνια", "πουκάμισα", "παπούτσια"],
    "σακάκι": ["παντελόνια", "πουκάμισα", "παπούτσια"],
    "blazers": ["pants", "shirts", "shoes"],
    "blazer": ["pants", "shirts", "shoes"],
    "jackets": ["pants", "shirts", "shoes", "scarves"],
    "jacket": ["pants", "shirts", "shoes", "scarves"],
    "παντελόνια": ["μπλούζες", "πουκάμισα", "παπούτσια", "ζώνες"],
    "παντελόνι": ["μπλούζες", "πουκάμισα", "παπούτσια", "ζώνες"],
    "pants": ["tops", "shirts", "shoes", "belts"],
    "trousers": ["tops", "shirts", "shoes", "belts"],
    "πουκάμισα": ["παντελόνια", "σακάκια", "παπούτσια"],
    "πουκάμισο": ["παντελόνια", "σακάκια", "παπούτσια"],
    "shirts": ["pants", "blazers", "shoes"],
    "shirt": ["pants", "blazers", "shoes"],
    "μπλούζες": ["παντελόνια", "φούστες", "αξεσουάρ"],
    "μπλούζα": ["παντελόνια", "φούστες", "αξεσουάρ"],
    "tops": ["pants", "skirts", "accessories"],
    "top": ["pants", "skirts", "accessories"],
    "φούστες": ["μπλούζες", "παπούτσια", "τσάντες"],
    "φούστα": ["μπλούζες", "παπούτσια", "τσάντες"],
    "skirts": ["tops", "shoes", "bags"],
    "skirt": ["tops", "shoes", "bags"],
    "παπούτσια": ["τσάντες", "αξεσουάρ"],
    "shoes": ["bags", "accessories"],
    "τσάντες": ["αξεσουάρ", "κοσμήματα"],
    "τσάντα": ["αξεσουάρ", "κοσμήματα"],
    "bags": ["accessories", "jewelry"],
    "bag": ["accessories", "jewelry"],
    "παλτό": ["μπότες", "αξεσουάρ", "κασκόλ"],
    "παλτά": ["μπότες", "αξεσουάρ", "κασκόλ"],
    "coats": ["boots", "accessories", "scarves"],
    "coat": ["boots", "accessories", "scarves"],
}

_DEFAULT_COMPLEMENTS = ["αξεσουάρ", "παπούτσια", "τσάντες"]


class CompleteLookService:
    def __init__(self) -> None:
        self._search = HybridSearchService()

    async def complete_the_look(
        self,
        sku: str,
        budget_max: float | None = None,
        occasion: str | None = None,
        style: str | None = None,
        top_k_per_group: int = 3,
    ) -> dict[str, Any]:
        anchor = self._find_anchor(sku)
        if not anchor:
            return {
                "anchor_sku": sku,
                "anchor_name": "",
                "anchor_categories": [],
                "groups": [],
                "message": f"Product {sku} not found in catalog.",
            }

        complement_cats = self._resolve_complements(anchor["categories"])
        groups: list[dict[str, Any]] = []

        for target_cat in complement_cats:
            filters = HybridSearchFilters(category=target_cat)
            if budget_max is not None:
                filters.max_price = budget_max
            if occasion:
                filters.occasion = occasion
            if style:
                filters.style = style

            query = f"{target_cat} για {anchor['name']}"
            results = await self._search.search(
                query=query, top_k=top_k_per_group * 2, filters=filters,
            )
            filtered = [r for r in results if r.sku != sku][:top_k_per_group]
            if filtered:
                groups.append({
                    "target_category": target_cat,
                    "items": [r.model_dump() for r in filtered],
                })

        return {
            "anchor_sku": anchor["sku"],
            "anchor_name": anchor["name"],
            "anchor_categories": anchor["categories"],
            "groups": groups,
        }

    @staticmethod
    def _find_anchor(sku: str) -> dict[str, Any] | None:
        store = get_chunk_store()
        for chunk in store.values():
            if chunk.sku == sku:
                return {
                    "sku": chunk.sku,
                    "name": chunk.name,
                    "categories": chunk.categories or [],
                    "price": chunk.price,
                }
        return None

    @staticmethod
    def _resolve_complements(categories: list[str]) -> list[str]:
        complements: list[str] = []
        seen: set[str] = set()
        for cat in categories:
            cat_lower = cat.lower().strip()
            for key, targets in _CATEGORY_COMPLEMENTS.items():
                if key in cat_lower:
                    for t in targets:
                        if t.lower() not in seen:
                            seen.add(t.lower())
                            complements.append(t)
        if not complements:
            complements = list(_DEFAULT_COMPLEMENTS)
        return complements[:5]
