from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.cache_service import CacheService, NS_PRODUCTS, NS_PRODUCT_DETAIL

logger = get_logger(__name__)

PRODUCTS_QUERY = """
query GetProducts($pageSize: Int!, $currentPage: Int!, $search: String, $filter: ProductAttributeFilterInput, $sort: ProductAttributeSortInput) {
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

# Minimal fields for incremental sync: detect changed SKUs without full payload.
PRODUCTS_MINIMAL_QUERY = """
query GetProductsMinimal($pageSize: Int!, $currentPage: Int!, $search: String, $sort: ProductAttributeSortInput) {
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

# Optional: use if your Magento exposes updated_at as a filter (custom attribute).
PRODUCTS_UPDATED_SINCE_QUERY = """
query GetProductsUpdatedSince($pageSize: Int!, $currentPage: Int!, $updatedFrom: String) {
  products(
    pageSize: $pageSize
    currentPage: $currentPage
    filter: { updated_at: { from: $updatedFrom } }
  ) {
    total_count
    items {
      sku
      name
      url_key
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

PRODUCT_BY_SKU_QUERY = """
query GetProductBySKU($sku: String!) {
  products(search: $sku, pageSize: 10) {
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
      description { html }
      short_description { html }
      url_rewrites { url }
      categories { name url_path }
      image { url label }
      media_gallery { url label }
      stock_status
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
            "User-Agent": "MostyAPI/2.0 (RAG sync)",
        }
        if getattr(self._settings, "MAGENTO_ACCESS_TOKEN", ""):
            headers["Authorization"] = f"Bearer {self._settings.MAGENTO_ACCESS_TOKEN}"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        result = data.get("data")

        if "errors" in data:
            if result:
                logger.warning(
                    "GraphQL partial errors (data still usable, %d error(s) skipped): %s",
                    len(data["errors"]),
                    [e.get("message", "") for e in data["errors"]],
                )
            else:
                first_msg = data["errors"][0].get("message", "Unknown GraphQL error")
                raise RuntimeError(f"GraphQL error: {first_msg}")

        return result or {}

    async def fetch_products(
        self,
        page: int = 1,
        per_page: int = 50,
        search: str | None = None,
        category: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        cache_key = f"p{page}:pp{per_page}:s{search}:c{category}:mn{min_price}:mx{max_price}"
        cached = self._cache.get(NS_PRODUCTS, cache_key)
        if cached is not None:
            return cached

        variables: dict[str, Any] = {
            "pageSize": per_page,
            "currentPage": page,
        }

        if search:
            variables["search"] = search

        gql_filter: dict[str, Any] = {}
        if category:
            gql_filter["category_url_path"] = {"eq": category}
        if min_price is not None or max_price is not None:
            price_filter: dict[str, Any] = {}
            if min_price is not None:
                price_filter["from"] = str(min_price)
            if max_price is not None:
                price_filter["to"] = str(max_price)
            gql_filter["price"] = price_filter

        if not search and not gql_filter:
            variables["search"] = ""

        if gql_filter:
            variables["filter"] = gql_filter

        data = await self.execute(PRODUCTS_QUERY, variables)
        products_data = data.get("products") or {}
        raw_items = products_data.get("items") or []
        items = [i for i in raw_items if i is not None]
        if len(items) < len(raw_items):
            logger.warning(
                "Filtered %d null items from page %d (unsupported product types)",
                len(raw_items) - len(items), page,
            )
        total = products_data.get("total_count", 0)

        self._cache.set(
            NS_PRODUCTS, cache_key, (items, total),
            ttl=self._settings.CACHE_PRODUCTS_TTL,
        )

        return items, total

    async def fetch_product_by_sku(self, sku: str) -> dict[str, Any] | None:
        cached = self._cache.get(NS_PRODUCT_DETAIL, sku)
        if cached is not None:
            return cached

        data = await self.execute(PRODUCT_BY_SKU_QUERY, {"sku": sku})
        items = data.get("products", {}).get("items", [])
        result = None
        for item in items:
            if item.get("sku") == sku:
                result = item
                break
        if result is None and items:
            result = items[0]

        if result:
            self._cache.set(
                NS_PRODUCT_DETAIL, sku, result,
                ttl=self._settings.CACHE_PRODUCTS_TTL,
            )
        return result

    async def fetch_all_products(self, per_page: int = 50) -> list[dict[str, Any]]:
        """Fetch every product via paginated calls."""
        all_items: list[dict[str, Any]] = []
        page = 1
        max_pages = 500
        while page <= max_pages:
            try:
                items, total = await self.fetch_products(page=page, per_page=per_page)
            except Exception as exc:
                logger.warning("Page %d fetch failed (%s), stopping pagination", page, exc)
                break
            all_items.extend(items)
            logger.info(
                "Fetched page %d — %d items (total so far: %d / %d)",
                page, len(items), len(all_items), total,
            )
            if len(all_items) >= total or not items:
                break
            page += 1
        return all_items

    async def fetch_products_minimal_page(
        self,
        page: int = 1,
        per_page: int = 100,
        sort: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Fetch one page of minimal product data (sku, updated_at, etc.) for sync scanning.
        Uses search='' to satisfy Magento's requirement; returns all products in pages.
        """
        variables: dict[str, Any] = {
            "pageSize": per_page,
            "currentPage": page,
            "search": "",
        }
        if sort:
            variables["sort"] = sort
        data = await self.execute(PRODUCTS_MINIMAL_QUERY, variables)
        products_data = data.get("products") or {}
        items = [i for i in (products_data.get("items") or []) if i is not None]
        total = products_data.get("total_count", 0)
        return items, total

    async def fetch_changed_product_skus_since(
        self,
        since_iso: str,
        per_page: int = 100,
        max_pages: int = 1000,
    ) -> list[dict[str, Any]]:
        """Return list of minimal records {sku, updated_at, created_at, stock_status}
        for products that were created or updated since the given ISO timestamp.
        Paginates through minimal query and filters in memory (Magento often has no updated_at filter).
        """
        changed: list[dict[str, Any]] = []
        page = 1
        while page <= max_pages:
            try:
                items, total = await self.fetch_products_minimal_page(
                    page=page, per_page=per_page
                )
            except Exception as exc:
                logger.warning(
                    "Minimal page %d failed (%s), stopping",
                    page, exc,
                )
                break
            for item in items:
                updated = (item.get("updated_at") or "").strip()
                created = (item.get("created_at") or "").strip()
                if updated >= since_iso or created >= since_iso:
                    changed.append(item)
            if not items or len(items) < per_page:
                break
            page += 1
        logger.info(
            "Found %d product(s) created/updated since %s (scanned %d pages)",
            len(changed), since_iso, page,
        )
        return changed

    async def fetch_changed_products_full(
        self,
        since_iso: str,
        per_page: int = 100,
        batch_size: int = 20,
    ) -> list[dict[str, Any]]:
        """Fetch full product payloads for all products changed since since_iso.
        Uses minimal scan then full fetch by SKU in batches.
        """
        minimal_list = await self.fetch_changed_product_skus_since(
            since_iso=since_iso, per_page=per_page
        )
        if not minimal_list:
            return []
        skus = [m["sku"] for m in minimal_list if m.get("sku")]
        skus = list(dict.fromkeys(skus))
        full_products: list[dict[str, Any]] = []
        for i in range(0, len(skus), batch_size):
            batch_skus = skus[i : i + batch_size]
            # Bypass cache so we get fresh data for indexing
            for sku in batch_skus:
                try:
                    data = await self.execute(
                        PRODUCT_BY_SKU_QUERY, {"sku": sku}
                    )
                    items = (data.get("products") or {}).get("items") or []
                    for item in items:
                        if item and item.get("sku") == sku:
                            full_products.append(item)
                            break
                except Exception as exc:
                    logger.warning("Failed to fetch full product %s: %s", sku, exc)
        return full_products
