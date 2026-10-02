"""Incremental index update: only process new/updated products from Magento.

- Fetches products changed since last sync (by updated_at).
- Adds new products, updates changed ones, removes out-of-stock from active index.
- Uses enriched chunk text for fashion retrieval.
- Safe for cron: batching, per-product error handling, atomic state save.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import RAGChunk
from app.services.graphql_service import GraphQLService
from app.services.rag_ingestion_service import get_chunk_store, save_chunk_store
from app.services.sync_state_service import (
    get_since_timestamp,
    load_sync_state,
    save_sync_state,
)
from app.services.vertex_service import VertexEmbeddingService, VertexVectorSearchService
from app.utils.chunking import chunk_rag_document
from app.utils.transformers import (
    build_enriched_product_text,
    normalize_product,
    product_to_rag_document,
)

logger = get_logger(__name__)


# Stock status that means product is not available for recommendation
OUT_OF_STOCK_VALUES = frozenset({"OUT_OF_STOCK", "out_of_stock", "0"})


def _is_in_stock(stock_status: str | None) -> bool:
    if not stock_status:
        return False
    return (stock_status or "").strip().upper() not in OUT_OF_STOCK_VALUES


class IncrementalIndexService:
    """Orchestrates incremental sync: fetch changed products, update embeddings and index."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._gql = GraphQLService()
        self._embedder = VertexEmbeddingService()
        self._vector_store = VertexVectorSearchService()
        self._chunk_store = get_chunk_store()
        self._batch_size = getattr(
            self._settings,
            "INDEX_BATCH_SIZE",
            int(self._settings.MAGENTO_PAGE_SIZE or 50),
        )

    async def _remove_sku_from_index(self, sku: str) -> int:
        """Remove all chunks for this SKU from vector store and chunk store. Returns count removed."""
        chunk_ids = [cid for cid, c in self._chunk_store.items() if c.sku == sku]
        if not chunk_ids:
            return 0
        await self._vector_store.remove_vectors(chunk_ids)
        for cid in chunk_ids:
            self._chunk_store.pop(cid, None)
        return len(chunk_ids)

    async def _process_one_product(
        self,
        raw: dict[str, Any],
    ) -> tuple[str, str, int]:
        """Process one product: remove old chunks, build enriched chunks, embed, upsert.
        Returns (sku, 'added'|'updated'|'removed', chunk_count).
        """
        sku = (raw.get("sku") or "").strip()
        if not sku:
            return "", "skipped", 0
        stock_status = raw.get("stock_status")
        if not _is_in_stock(stock_status):
            removed = await self._remove_sku_from_index(sku)
            return sku, "removed", removed

        product = normalize_product(raw)
        doc = product_to_rag_document(product, stock_status=stock_status)
        enriched_text = build_enriched_product_text(doc, raw_product=raw)
        chunks = chunk_rag_document(doc, enriched_text)
        if not chunks:
            return sku, "skipped", 0

        # Remove existing chunks for this SKU (dedupe: one set of chunks per SKU)
        removed_before = await self._remove_sku_from_index(sku)
        action = "added" if removed_before == 0 else "updated"

        texts = [c.text for c in chunks]
        embeddings = await self._embedder.embed_texts(texts)
        ids = [c.chunk_id for c in chunks]
        metadata_list = [
            {
                "sku": c.sku,
                "name": c.name,
                "url": c.url or "",
                "categories": c.categories,
                "price": str(c.price) if c.price else "",
                "source": c.source,
            }
            for c in chunks
        ]
        await self._vector_store.upsert_vectors(ids, embeddings, metadata_list)
        for c in chunks:
            self._chunk_store[c.chunk_id] = c

        return sku, action, len(chunks)

    async def run_incremental_sync(
        self,
        since_iso: str | None = None,
        *,
        reconcile_deletes: bool = False,
    ) -> dict[str, Any]:
        """Run one incremental sync pass.
        - since_iso: only process products updated after this (default: from sync state).
        - reconcile_deletes: if True, fetch all Magento SKUs and remove index entries for SKUs no longer in Magento.
        Returns stats: indexed_count, updated_count, removed_count, failed_count, products_processed.
        """
        state = load_sync_state()
        since = since_iso or get_since_timestamp(state)
        batch_size = self._batch_size
        stats = {
            "indexed_count": 0,
            "updated_count": 0,
            "removed_count": 0,
            "failed_count": 0,
            "products_processed": 0,
            "chunks_added": 0,
            "chunks_removed": 0,
        }

        try:
            full_products = await self._gql.fetch_changed_products_full(
                since_iso=since,
                per_page=min(100, batch_size * 2),
                batch_size=batch_size,
            )
        except Exception as e:
            logger.exception("Failed to fetch changed products from Magento: %s", e)
            save_sync_state(state, last_error=str(e))
            raise

        for raw in full_products:
            try:
                sku, action, count = await self._process_one_product(raw)
                stats["products_processed"] += 1
                if action == "updated":
                    stats["updated_count"] += 1
                    stats["chunks_added"] += count
                elif action == "removed":
                    stats["removed_count"] += 1
                    stats["chunks_removed"] += count
                elif action == "added":
                    stats["indexed_count"] += 1
                    stats["chunks_added"] += count
            except Exception as e:
                stats["failed_count"] += 1
                logger.warning("Failed to process product %s: %s", raw.get("sku"), e)

        # Optional: remove index entries for products no longer in Magento
        if reconcile_deletes:
            try:
                minimal = await self._gql.fetch_changed_product_skus_since(
                    since_iso="1970-01-01T00:00:00Z",
                    per_page=100,
                    max_pages=5000,
                )
                magento_skus = {m["sku"] for m in minimal if m.get("sku")}
                indexed_skus = {c.sku for c in self._chunk_store.values()}
                for sku in indexed_skus - magento_skus:
                    removed = self._remove_sku_from_index(sku)
                    stats["chunks_removed"] += removed
                    if removed:
                        stats["removed_count"] += 1
            except Exception as e:
                logger.warning("Reconcile deletes failed (non-fatal): %s", e)

        # Rebuild BM25 index from current chunk store
        try:
            from app.services.hybrid_search_service import rebuild_bm25_index
            rebuild_bm25_index()
        except Exception as e:
            logger.warning("BM25 rebuild after incremental sync failed: %s", e)

        save_chunk_store()
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        save_sync_state(
            state,
            last_sync_at=now_iso,
            last_successful_sync_at=now_iso,
            indexed_skus=list({c.sku for c in self._chunk_store.values()}),
            last_error=None,
        )
        logger.info(
            "Incremental sync done: processed=%d updated=%d removed=%d failed=%d chunks_added=%d chunks_removed=%d",
            stats["products_processed"],
            stats["updated_count"],
            stats["removed_count"],
            stats["failed_count"],
            stats["chunks_added"],
            stats["chunks_removed"],
        )
        return stats