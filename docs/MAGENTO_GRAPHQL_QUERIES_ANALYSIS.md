# Magento GraphQL Product Queries – Codebase Analysis

This document answers where each product query is used, the exact runtime flows, and whether each query is active or unused. All conclusions are from tracing the actual code (no theoretical answers).

---

## 1. Per-query summary

| Query | Purpose | Called from | When executed | Active? | Recommendation |
|-------|---------|-------------|---------------|---------|----------------|
| **PRODUCTS_QUERY** | Full product list (paginated, filterable) | `graphql_service.fetch_products()` | Full indexing, REST product list API | **Active** | **Keep** – primary for full sync and product listing |
| **PRODUCTS_MINIMAL_QUERY** | Lightweight list (sku, updated_at, created_at, stock_status) | `graphql_service.fetch_products_minimal_page()` | Incremental sync (changed-SKU scan), reconcile deletes | **Active** | **Keep** – primary for cron/incremental |
| **PRODUCTS_UPDATED_SINCE_QUERY** | Filter products by `updated_at` (server-side) | **Nowhere** | Never | **Unused** | **Remove or keep as optional fallback** (see §5) |
| **PRODUCT_BY_SKU_QUERY** | Single product by SKU (full payload) | `graphql_service.fetch_product_by_sku()`, `graphql_service.fetch_changed_products_full()` (per SKU) | Product detail API, live price/stock, single-product RAG sync, incremental full-payload fetch | **Active** | **Keep** – exact product refresh and recommendations data |

---

## 2. Where each query is used (file/function)

### PRODUCTS_QUERY

- **Definition:** `app/services/graphql_service.py` (lines 13–67).
- **Used in:** `GraphQLService.fetch_products()` (line 278).
- **Call chain:**
  - **Full indexing:**  
    `rag_ingestion_service.RAGIngestionService.sync_all()`  
    → `graphql_service.fetch_all_products(per_page=50)`  
    → `fetch_products(page, per_page)` in a loop  
    → `execute(PRODUCTS_QUERY, variables)`.
  - **REST product list:**  
    `routers/products.list_products()`  
    → `product_service.ProductService.list_products()`  
    → `graphql_service.fetch_products(page, per_page, search, category, min_price, max_price)`  
    → `execute(PRODUCTS_QUERY, variables)`.
- **When:** On every full RAG sync (API `POST /api/v1/rag/sync`, jobs “sync-all”, scheduler full sync) and on every `GET /api/v1/products` (with optional filters).

### PRODUCTS_MINIMAL_QUERY

- **Definition:** `app/services/graphql_service.py` (lines 71–87).
- **Used in:** `GraphQLService.fetch_products_minimal_page()` (line 355).
- **Call chain:**
  - **Incremental sync (cron / scheduler):**  
    `scripts/update_index.py` or `scheduler_service._run_incremental_sync()`  
    → `incremental_index_service.IncrementalIndexService.run_incremental_sync()`  
    → `graphql_service.fetch_changed_products_full(since_iso)`  
    → `fetch_changed_product_skus_since(since_iso)`  
    → loop over pages: `fetch_products_minimal_page(page, per_page)`  
    → `execute(PRODUCTS_MINIMAL_QUERY, variables)`.
  - **Reconcile deletes:**  
    Same `run_incremental_sync(reconcile_deletes=True)`  
    → `fetch_changed_product_skus_since("1970-01-01T00:00:00Z", …)`  
    → same minimal query (all products in pages).
- **When:** Every incremental index run (cron or “run-now/incremental”) to get changed SKUs; and when reconciling deletes to get the full Magento SKU set.

### PRODUCTS_UPDATED_SINCE_QUERY

- **Definition:** `app/services/graphql_service.py` (lines 91–141).
- **Used in:** **No function references this query.** Grep shows only the definition.
- **When:** Never executed.
- **Note:** It uses `filter: { updated_at: { from: $updatedFrom } }`. Standard Magento 2 GraphQL `ProductAttributeFilterInput` does not expose `updated_at` unless the project added it; so even if wired, it would likely fail or return nothing on vanilla Magento.

### PRODUCT_BY_SKU_QUERY

- **Definition:** `app/services/graphql_service.py` (lines 144–193).
- **Used in:**
  1. `GraphQLService.fetch_product_by_sku(sku)` (line 301) – uses cache.
  2. `GraphQLService.fetch_changed_products_full()` (lines 420–421) – per-SKU full fetch, **bypasses cache** (calls `execute()` directly).
- **Call chain:**
  - **Product detail / live:**  
    `GET /api/v1/products/{sku}` or `GET /api/v1/products/{sku}/live`  
    → `product_service.get_product(sku)` / `get_product_live(sku)`  
    → `graphql_service.fetch_product_by_sku(sku)`  
    → `execute(PRODUCT_BY_SKU_QUERY, {"sku": sku})` (when not cached).
  - **Single-product RAG sync:**  
    `POST /api/v1/rag/sync/{sku}`  
    → `rag_ingestion_service.sync_product(sku)`  
    → `graphql_service.fetch_product_by_sku(sku)`  
    → same query (cached).
  - **Incremental sync (full payload for changed SKUs):**  
    `fetch_changed_products_full()`  
    → for each SKU in batch: `execute(PRODUCT_BY_SKU_QUERY, {"sku": sku})` (no cache).
- **When:** On product detail/live API calls, single-product RAG sync, and as the second step of incremental sync (after minimal scan) to load full product data for each changed SKU.

---

## 3. Which query is used for each flow

| Flow | Query used | How |
|------|------------|-----|
| **Full indexing** | **PRODUCTS_QUERY** | `sync_all()` → `fetch_all_products()` → repeated `fetch_products()` (paginated) with `PRODUCTS_QUERY`. |
| **Incremental sync** | **PRODUCTS_MINIMAL_QUERY** + **PRODUCT_BY_SKU_QUERY** | Minimal query (paginated) to get changed SKUs since `last_sync_at`; then one `PRODUCT_BY_SKU_QUERY` per changed SKU for full payload. |
| **Cron updates** | Same as incremental | `scripts/update_index.py` → `run_incremental_sync()` → same as above. |
| **Product lookup by SKU** | **PRODUCT_BY_SKU_QUERY** | `fetch_product_by_sku(sku)` (used by product detail, live, and single-product RAG sync). |
| **Retrieval for recommendations** | **None** | Recommendations use the in-memory chunk store + Vertex index + BM25; no GraphQL at request time. |

---

## 4. Unused, redundant, or overlapping

- **PRODUCTS_UPDATED_SINCE_QUERY:** **Unused.** Defined but never called. Overlaps in *intent* with “changed since” incremental flow, but that flow is implemented with **PRODUCTS_MINIMAL_QUERY** (scan all pages, filter in memory by `updated_at` / `created_at`).
- **PRODUCTS_QUERY vs PRODUCTS_MINIMAL_QUERY:** Not redundant. Full sync uses **PRODUCTS_QUERY** (full fields); incremental uses **PRODUCTS_MINIMAL_QUERY** (lightweight) then **PRODUCT_BY_SKU_QUERY** only for changed SKUs.
- **PRODUCT_BY_SKU_QUERY:** Used both for live API/single-product sync (cached) and for incremental full-payload fetch (uncached). No duplicate query; single query, two call sites.

---

## 5. Does PRODUCTS_UPDATED_SINCE_QUERY work with your Magento schema?

- **In standard Magento 2 GraphQL**, `ProductAttributeFilterInput` does **not** include `updated_at`. So `filter: { updated_at: { from: $updatedFrom } }` is **not** supported in the default schema and would typically cause a validation error or be ignored.
- **Conclusion:** The query is both **unused** and **unlikely to work** on vanilla Magento. It would only work if your project has added `updated_at` as a filterable attribute in the GraphQL schema.

---

## 6. Exact runtime flow when the system updates products

**Incremental (cron or “run-now/incremental”):**

1. **Entry:** `scripts/update_index.py` (cron) or `POST /api/v1/rag/scheduler/run-now/incremental` → `scheduler_service.trigger_incremental_sync_now()` → `_run_incremental_sync()`.
2. **Lock:** Script acquires lock (or scheduler holds `_lock`).
3. **State:** `sync_state_service.load_sync_state()` → `get_since_timestamp(state)` → `since_iso` (e.g. `last_successful_sync_at` or `1970-01-01T00:00:00Z`).
4. **Changed SKUs:** `graphql_service.fetch_changed_products_full(since_iso)`:
   - `fetch_changed_product_skus_since(since_iso)`:
     - Pages with `fetch_products_minimal_page()` → **PRODUCTS_MINIMAL_QUERY** (`search: ""`).
     - In-memory filter: keep items with `updated_at >= since_iso` or `created_at >= since_iso`.
   - For each such SKU (in batches): `execute(PRODUCT_BY_SKU_QUERY, {"sku": sku})` → **PRODUCT_BY_SKU_QUERY** (no cache).
5. **Index update:** For each full product: if out of stock → remove from index; else remove old chunks for SKU, build enriched chunks, embed, upsert to Vertex and chunk store.
6. **Optional reconcile:** If `reconcile_deletes`: `fetch_changed_product_skus_since("1970-01-01...")` → **PRODUCTS_MINIMAL_QUERY** again (all SKUs), then remove from index any SKU not in Magento.
7. **Finish:** Rebuild BM25, `save_chunk_store()`, `save_sync_state()`, release lock.

**Full sync:**

1. **Entry:** `POST /api/v1/rag/sync` or job “sync-all” or scheduler full sync → `RAGIngestionService.sync_all()`.
2. **Fetch:** `fetch_all_products(per_page=50)` → loop `fetch_products(page, per_page)` → **PRODUCTS_QUERY** each time.
3. **Index:** For each product: `_process_product()` (chunk + embed) → `_store_chunks()` (Vertex + chunk store). No per-SKU delete before add (chunk store is effectively replaced by full set over the run).
4. **Finish:** Rebuild BM25, invalidate caches, `_save_chunk_store()`.

---

## 7. New product added

- **Magento:** New product gets `created_at` and `updated_at`.
- **Next incremental run:**  
  `fetch_changed_product_skus_since(since_iso)` returns this product (because `created_at >= since_iso` or `updated_at >= since_iso`).  
  `fetch_changed_products_full()` loads full payload via **PRODUCT_BY_SKU_QUERY**.  
  Product is in stock → chunks created and upserted; product appears in recommendations.
- **Full sync:** Product is included in **PRODUCTS_QUERY** pages and is ingested like any other product.

---

## 8. Existing product updated

- **Magento:** Product’s `updated_at` (and possibly data) changes.
- **Next incremental run:** Same as §7: minimal scan includes it (updated_at >= since), full fetch by **PRODUCT_BY_SKU_QUERY**, then old chunks for that SKU are removed and new chunks (with new text/embedding) are upserted. One active set of chunks per SKU.
- **Full sync:** Updated product is fetched again via **PRODUCTS_QUERY** and re-ingested (chunks replaced as the full sync rebuilds the set).

---

## 9. Product goes out of stock

- **Incremental:** For that product, `fetch_changed_products_full()` returns it (once it’s “changed”); `_process_one_product()` sees `stock_status` not in stock → calls `_remove_sku_from_index(sku)`: all chunks for that SKU are removed from Vertex and chunk store. It will no longer appear in recommendations.
- **Full sync:** Same product is still in **PRODUCTS_QUERY** results. Current `sync_all()` does **not** exclude out-of-stock; it would re-add the product. So after a full sync, out-of-stock products can reappear in the index unless you add a filter in full sync (e.g. skip or remove when `stock_status` is out of stock).

---

## 10. Current strategy: full reindex vs incremental vs SKU-by-SKU vs mixed

- **Full reindex:** Used when:
  - `POST /api/v1/rag/sync`
  - Job “sync-all”
  - Scheduler “full sync” (cron)
  Uses **PRODUCTS_QUERY** only (paginated full catalog).
- **Incremental sync:** Used when:
  - Cron runs `scripts/update_index.py`
  - Scheduler “incremental” (interval) or `POST .../run-now/incremental`
  Uses **PRODUCTS_MINIMAL_QUERY** (changed-SKU scan) + **PRODUCT_BY_SKU_QUERY** (full payload per changed SKU). No full catalog fetch for incremental.
- **SKU-by-SKU refresh:** Used when:
  - `POST /api/v1/rag/sync/{sku}` (single product sync)
  Uses only **PRODUCT_BY_SKU_QUERY** (via `fetch_product_by_sku(sku)`).
- **Mixed:** The system uses **both** full reindex and incremental (and single-SKU) as above. They are separate entrypoints; incremental does not call **PRODUCTS_QUERY**.

---

## 11. Recommended architecture for production

| Role | Query | Reason |
|------|--------|--------|
| **Primary for cron (incremental) sync** | **PRODUCTS_MINIMAL_QUERY** | Lightweight scan of full catalog in pages; filter by `updated_at`/`created_at` in memory. No dependency on Magento exposing `updated_at` filter. |
| **Fallback for incremental** | **PRODUCTS_UPDATED_SINCE_QUERY** | **Optional.** Only if your Magento schema adds `updated_at` to `ProductAttributeFilterInput`. Then you could add a method that tries this query first (e.g. one page with `filter: { updated_at: { from: since } }`); if it fails or returns empty and you know Magento doesn’t support it, fall back to minimal scan. As-is, **PRODUCTS_UPDATED_SINCE_QUERY** is unused and can be **removed** unless you implement that optional path. |
| **Full indexing** | **PRODUCTS_QUERY** | Single source for full catalog with all fields needed for chunking/embedding; supports pagination and existing filters (category, price, etc.). |
| **Exact product refresh** | **PRODUCT_BY_SKU_QUERY** | Single product by SKU for API detail/live, single-product RAG sync, and incremental full-payload fetch after minimal scan. |

**Concrete recommendations:**

1. **Keep:** **PRODUCTS_QUERY**, **PRODUCTS_MINIMAL_QUERY**, **PRODUCT_BY_SKU_QUERY** – each has a clear, used role.
2. **Remove or gate:** **PRODUCTS_UPDATED_SINCE_QUERY** – either delete it or keep it behind a “try server-side updated_at filter” branch that falls back to the current minimal scan; only enable that branch if your Magento schema supports `updated_at` filter.
3. **Cron:** Keep using **PRODUCTS_MINIMAL_QUERY** + **PRODUCT_BY_SKU_QUERY** as today (no change).
4. **Full indexing:** Keep using **PRODUCTS_QUERY** only (no change).
5. **Exact refresh:** Keep using **PRODUCT_BY_SKU_QUERY** (no change).
6. **Optional improvement:** In full sync, skip or remove out-of-stock products so they don’t reappear after a full reindex (align with incremental behavior).
