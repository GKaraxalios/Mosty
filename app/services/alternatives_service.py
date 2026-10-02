"""Similar / Cheaper / Premium alternatives service.

Given an anchor product (SKU), finds alternatives in three tiers:
- similar: same category, comparable price range
- cheaper: same category, lower price
- premium: same category, higher price

Reuses the existing HybridSearchService for retrieval.
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.models.schemas import HybridSearchFilters, RAGChunk
from app.services.hybrid_search_service import HybridSearchService
from app.services.rag_ingestion_service import get_chunk_store

logger = get_logger(__name__)

_PRICE_BAND_FACTOR_LOW = 0.7
_PRICE_BAND_FACTOR_HIGH = 1.3


class AlternativesService:
    def __init__(self) -> None:
        self._search = HybridSearchService()

    async def find_alternatives(
        self,
        sku: str,
        types: list[str] | None = None,
        top_k: int = 4,
    ) -> dict[str, Any]:
        types = types or ["similar", "cheaper", "premium"]
        anchor = self._find_anchor(sku)
        if not anchor:
            return {
                "anchor_sku": sku,
                "anchor_name": "",
                "groups": [],
                "message": f"Product {sku} not found in catalog.",
            }

        groups: list[dict[str, Any]] = []

        primary_cat = anchor["categories"][0] if anchor["categories"] else None

        if "similar" in types:
            items = await self._search_tier(
                anchor, primary_cat, "similar", top_k,
            )
            groups.append({
                "alt_type": "similar",
                "reason": f"Παρόμοια προϊόντα με το {anchor['name']}",
                "items": items,
            })

        if "cheaper" in types:
            items = await self._search_tier(
                anchor, primary_cat, "cheaper", top_k,
            )
            groups.append({
                "alt_type": "cheaper",
                "reason": f"Πιο οικονομικές εναλλακτικές για {anchor['name']}",
                "items": items,
            })

        if "premium" in types:
            items = await self._search_tier(
                anchor, primary_cat, "premium", top_k,
            )
            groups.append({
                "alt_type": "premium",
                "reason": f"Premium εναλλακτικές για {anchor['name']}",
                "items": items,
            })

        return {
            "anchor_sku": anchor["sku"],
            "anchor_name": anchor["name"],
            "groups": groups,
        }

    async def _search_tier(
        self,
        anchor: dict[str, Any],
        primary_cat: str | None,
        tier: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        filters = HybridSearchFilters()
        if primary_cat:
            filters.category = primary_cat

        price = anchor.get("price")
        if price is not None:
            if tier == "similar":
                filters.min_price = round(price * _PRICE_BAND_FACTOR_LOW, 2)
                filters.max_price = round(price * _PRICE_BAND_FACTOR_HIGH, 2)
            elif tier == "cheaper":
                filters.max_price = round(price * 0.95, 2)
            elif tier == "premium":
                filters.min_price = round(price * 1.05, 2)

        query = anchor["name"]
        results = await self._search.search(
            query=query, top_k=top_k * 2, filters=filters,
        )
        filtered = [r for r in results if r.sku != anchor["sku"]]
        seen_skus: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for r in filtered:
            if r.sku not in seen_skus:
                seen_skus.add(r.sku)
                deduped.append(r.model_dump())
            if len(deduped) >= top_k:
                break
        return deduped

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
