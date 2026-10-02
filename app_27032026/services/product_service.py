from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.models.schemas import (
    PriceSchema,
    ProductDetailResponse,
    ProductListResponse,
    ProductLiveResponse,
    ProductSchema,
)
from app.services.graphql_service import GraphQLService
from app.utils.transformers import normalize_product

logger = get_logger(__name__)


class ProductService:
    def __init__(self) -> None:
        self._gql = GraphQLService()

    async def list_products(
        self,
        page: int = 1,
        per_page: int = 50,
        search: str | None = None,
        category: str | None = None,
        brand: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        updated_after: str | None = None,
        lang: str | None = None,
    ) -> ProductListResponse:
        items_raw, total = await self._gql.fetch_products(
            page=page,
            per_page=per_page,
            search=search,
            category=category,
            min_price=min_price,
            max_price=max_price,
        )

        products = [normalize_product(p) for p in items_raw]

        # Fallback filtering for params not natively supported by Magento GQL
        if brand:
            brand_lower = brand.lower()
            products = [
                p for p in products
                if brand_lower in (p.name or "").lower()
                or brand_lower in (p.meta_keyword or "").lower()
            ]

        if updated_after:
            try:
                threshold = datetime.fromisoformat(updated_after.replace("Z", "+00:00"))
                products = [
                    p for p in products
                    if p.updated_at
                    and datetime.fromisoformat(p.updated_at.replace("Z", "+00:00"))
                    >= threshold
                ]
            except ValueError:
                logger.warning("Invalid updated_after value: %s", updated_after)

        total_pages = max(1, -(-total // per_page))  # ceiling division

        return ProductListResponse(
            page=page,
            per_page=per_page,
            total=total,
            total_pages=total_pages,
            items=products,
        )

    async def get_product(self, sku: str) -> ProductDetailResponse | None:
        raw = await self._gql.fetch_product_by_sku(sku)
        if not raw:
            return None
        product = normalize_product(raw)
        return ProductDetailResponse(product=product)

    async def get_product_live(self, sku: str) -> ProductLiveResponse | None:
        raw = await self._gql.fetch_product_by_sku(sku)
        if not raw:
            return None
        product = normalize_product(raw)
        stock_status = raw.get("stock_status")
        in_stock = stock_status == "IN_STOCK" if stock_status else None
        return ProductLiveResponse(
            sku=product.sku,
            price=product.price,
            url=product.url,
            updated_at=product.updated_at,
            in_stock=in_stock,
        )
