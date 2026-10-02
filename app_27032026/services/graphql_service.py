from __future__ import annotations

import math
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.cache_service import CacheService, NS_PRODUCTS, NS_PRODUCT_DETAIL

logger = get_logger(__name__)

PRODUCTS_QUERY = """
query GetProducts(
  $pageSize: Int!,
  $currentPage: Int!,
  $search: String,
  $filter: ProductAttributeFilterInput,
  $sort: ProductAttributeSortInput
) {
  products(
    pageSize: $pageSize
    currentPage: $currentPage
    search: $search
    filter: $filter
    sort: $sort
  ) {
    total_count
    items {
      sku
      name
      url_key
      __typename
      meta_keyword
      meta_description
      created_at
      updated_at
      stock_status
      description { html }
      short_description { html }
      url_rewrites { url }
      categories { name url_path }
      image { url label }
      media_gallery { url label }
      price_range {
        minimum_price {
          regular_price { value currency }
          final_price { value currency }
          discount { amount_off percent_off }
        }
      }
      ... on ConfigurableProduct {
        configurable_options {
          attribute_code
          label
          values { label value_index }
        }
        variants {
          product {
            sku
            name
            stock_status
            price_range {
              minimum_price {
                regular_price { value currency }
                final_price { value currency }
                discount { amount_off percent_off }
              }
            }
          }
        }
      }
    }
  }
}
"""

PRODUCTS_MINIMAL_QUERY = """
query GetProductsMinimal(
  $pageSize: Int!,
  $currentPage: Int!,
  $search: String,
  $sort: ProductAttributeSortInput
) {
  products(
    pageSize: $pageSize
    currentPage: $currentPage
    search: $search
    sort: $sort
  ) {
    total_count
    items {
      sku
      created_at
      updated_at
      stock_status
    }
  }
}
"""

PRODUCT_BY_SKU_QUERY = """
query GetProductBySKU($sku: String!) {
  products(search: $sku, pageSize: 20) {
    total_count
    items {
      sku
      name
      url_key
      __typename
      meta_keyword
      meta_description
      created_at
      updated_at
      stock_status
      description { html }
      short_description { html }
      url_rewrites { url }
      categories { name url_path }
      image { url label }
      media_gallery { url label }
      price_range {
        minimum_price {
          regular_price { value currency }
          final_price { value currency }
          discount { amount_off percent_off }
        }
      }
      ... on ConfigurableProduct {
        configurable_options {
          attribute_code
          label
          values { label value_index }
        }
        variants {
          product {
            sku
            name
            stock_status
            price_range {
              minimum_price {
                regular_price { value currency }
                final_price { value currency }
                discount { amount_off percent_off }
              }
            }
          }
        }
      }
    }
  }
}
"""


class GraphQLService:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._url = self._settings.MAGENTO_GRAPHQL_URL
        self._timeout = self._settings.MAGENTO_REQUEST_TIMEOUT
        self._cache = CacheService()

    async def execute(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        headers = {
            "Content-Type": "application/json",
            "Store": self._settings.MAGENTO_STORE_CODE,
            "User-Agent": "MostyAPI/2.0 (GraphQL sync)",
        }

        token = getattr(self._settings, "MAGENTO_ACCESS_TOKEN", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        result = data.get("data")

        if "errors" in data:
            if result:
                logger.warning(
                    "GraphQL partial errors (%d): %s",
                    len(data["errors"]),
                    [e.get("message", "") for e in data["errors"]],
                )
            else:
                first_msg = data["errors"][0].get("message", "Unknown GraphQL error")
                raise RuntimeError(f"GraphQL error: {first_msg}")

        return result or {}

    def _build_filter(
        self,
        *,
        category: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
    ) -> dict[str, Any]:
        gql_filter: dict[str, Any] = {}

        if category:
            gql_filter["category_url_path"] = {"eq": category}

        if min_price is not None or max_price is not None:
            price_filter: dict[str, str] = {}
            if min_price is not None:
                price_filter["from"] = str(min_price)
            if max_price is not None:
                price_filter["to"] = str(max_price)
            gql_filter["price"] = price_filter

        return gql_filter

    async def fetch_products(
        self,
        page: int = 1,
        per_page: int = 50,
        search: str | None = None,
        category: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        sort: dict[str, str] | None = None,
        use_cache: bool = True,
    ) -> tuple[list[dict[str, Any]], int]:
        cache_key = (
            f"p{page}:pp{per_page}:s{search}:c{category}:"
            f"mn{min_price}:mx{max_price}:sort{sort}"
        )

        if use_cache:
            cached = self._cache.get(NS_PRODUCTS, cache_key)
            if cached is not None:
                return cached

        variables: dict[str, Any] = {
            "pageSize": per_page,
            "currentPage": page,
        }

        if search is not None:
            variables["search"] = search

        gql_filter = self._build_filter(
            category=category,
            min_price=min_price,
            max_price=max_price,
        )

        if not search and not gql_filter:
            # Magento often wants search to exist when no filters are passed
            variables["search"] = ""

        if gql_filter:
            variables["filter"] = gql_filter

        if sort:
            variables["sort"] = sort

        data = await self.execute(PRODUCTS_QUERY, variables)
        products_data = data.get("products") or {}

        raw_items = products_data.get("items") or []
        items = [item for item in raw_items if item is not None]

        if len(items) < len(raw_items):
            logger.warning(
                "Filtered %d null items from page %d",
                len(raw_items) - len(items),
                page,
            )

        total = int(products_data.get("total_count") or 0)

        if use_cache:
            self._cache.set(
                NS_PRODUCTS,
                cache_key,
                (items, total),
                ttl=self._settings.CACHE_PRODUCTS_TTL,
            )

        return items, total

    async def fetch_product_by_sku(
        self,
        sku: str,
        use_cache: bool = True,
    ) -> dict[str, Any] | None:
        if use_cache:
            cached = self._cache.get(NS_PRODUCT_DETAIL, sku)
            if cached is not None:
                return cached

        data = await self.execute(PRODUCT_BY_SKU_QUERY, {"sku": sku})
        items = (data.get("products") or {}).get("items") or []

        result: dict[str, Any] | None = None

        for item in items:
            if item and item.get("sku") == sku:
                result = item
                break

        if result is None and items:
            result = items[0]

        if result and use_cache:
            self._cache.set(
                NS_PRODUCT_DETAIL,
                sku,
                result,
                ttl=self._settings.CACHE_PRODUCTS_TTL,
            )

        return result

    async def fetch_all_products(
        self,
        per_page: int = 100,
        search: str | None = None,
        category: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        sort: dict[str, str] | None = None,
        use_cache: bool = False,
        max_pages: int = 1000,
    ) -> list[dict[str, Any]]:
        """
        Fetch all products across all pages.

        This method is safe for full catalog sync / export use.
        It keeps requesting pages until:
        - Magento returns no items
        - a short page is returned
        - current page >= computed pages_needed
        - current page reaches max_pages
        """
        all_items: list[dict[str, Any]] = []
        page = 1
        pages_needed: int | None = None

        while page <= max_pages:
            try:
                items, total = await self.fetch_products(
                    page=page,
                    per_page=per_page,
                    search=search,
                    category=category,
                    min_price=min_price,
                    max_price=max_price,
                    sort=sort,
                    use_cache=use_cache,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to fetch page %d while loading all products: %s",
                    page,
                    exc,
                )
                break

            if pages_needed is None:
                pages_needed = max(1, math.ceil(total / per_page)) if total > 0 else 1

            logger.info(
                "Fetched page %d/%s - %d items (collected=%d, total_count=%d)",
                page,
                pages_needed,
                len(items),
                len(all_items) + len(items),
                total,
            )

            if not items:
                break

            all_items.extend(items)

            # If Magento returns a short page, this is the last page.
            if len(items) < per_page:
                break

            # If total_count gives us a reliable page ceiling, respect it.
            if pages_needed is not None and page >= pages_needed:
                break

            page += 1

        # Deduplicate by SKU to avoid edge cases from catalog anomalies
        deduped: list[dict[str, Any]] = []
        seen_skus: set[str] = set()

        for item in all_items:
            sku = (item.get("sku") or "").strip()
            if not sku:
                continue
            if sku in seen_skus:
                continue
            seen_skus.add(sku)
            deduped.append(item)

        logger.info(
            "Finished full product fetch: %d unique products collected",
            len(deduped),
        )
        return deduped

    async def fetch_products_minimal_page(
        self,
        page: int = 1,
        per_page: int = 100,
        sort: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        variables: dict[str, Any] = {
            "pageSize": per_page,
            "currentPage": page,
            "search": "",
        }

        if sort:
            variables["sort"] = sort

        data = await self.execute(PRODUCTS_MINIMAL_QUERY, variables)
        products_data = data.get("products") or {}

        raw_items = products_data.get("items") or []
        items = [item for item in raw_items if item is not None]
        total = int(products_data.get("total_count") or 0)

        return items, total

    async def fetch_changed_product_skus_since(
        self,
        since_iso: str,
        per_page: int = 100,
        max_pages: int = 1000,
    ) -> list[dict[str, Any]]:
        changed: list[dict[str, Any]] = []
        page = 1

        while page <= max_pages:
            try:
                items, total = await self.fetch_products_minimal_page(
                    page=page,
                    per_page=per_page,
                )
            except Exception as exc:
                logger.warning(
                    "Minimal scan failed on page %d: %s",
                    page,
                    exc,
                )
                break

            logger.info(
                "Minimal scan page %d - %d items (catalog total=%d)",
                page,
                len(items),
                total,
            )

            for item in items:
                updated = (item.get("updated_at") or "").strip()
                created = (item.get("created_at") or "").strip()
                if updated >= since_iso or created >= since_iso:
                    changed.append(item)

            if not items or len(items) < per_page:
                break

            pages_needed = max(1, math.ceil(total / per_page)) if total > 0 else 1
            if page >= pages_needed:
                break

            page += 1

        logger.info(
            "Found %d product(s) changed since %s",
            len(changed),
            since_iso,
        )
        return changed

    async def fetch_changed_products_full(
        self,
        since_iso: str,
        per_page: int = 100,
        batch_size: int = 20,
    ) -> list[dict[str, Any]]:
        minimal_list = await self.fetch_changed_product_skus_since(
            since_iso=since_iso,
            per_page=per_page,
        )

        if not minimal_list:
            return []

        skus = [item["sku"] for item in minimal_list if item.get("sku")]
        skus = list(dict.fromkeys(skus))

        full_products: list[dict[str, Any]] = []

        for i in range(0, len(skus), batch_size):
            batch_skus = skus[i:i + batch_size]

            for sku in batch_skus:
                try:
                    product = await self.fetch_product_by_sku(sku, use_cache=False)
                    if product:
                        full_products.append(product)
                except Exception as exc:
                    logger.warning("Failed to fetch full product for SKU %s: %s", sku, exc)

        logger.info(
            "Fetched %d full changed product(s) since %s",
            len(full_products),
            since_iso,
        )
        return full_products

    async def fetch_full_catalog_for_indexing(
        self,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Convenience helper for indexing/export jobs.
        Returns the full catalog using stable pagination.
        """
        return await self.fetch_all_products(
            per_page=per_page,
            use_cache=False,
            sort={"updated_at": "DESC"},
        )