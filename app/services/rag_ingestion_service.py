from __future__ import annotations

import json
import os
from typing import Any

from app.core.logging import get_logger
from app.utils.gcs_storage import path_exists, read_text, write_text
from app.models.schemas import (
    RAGChunk,
    RAGDeleteResponse,
    RAGSyncResponse,
    RAGSyncSingleResponse,
)
from app.services.cache_service import CacheService
from app.services.graphql_service import GraphQLService
from app.services.vertex_service import VertexEmbeddingService, VertexVectorSearchService
from app.utils.chunking import chunk_rag_document
from app.utils.transformers import (
    normalize_product,
    product_to_rag_document,
    rag_document_to_text,
)

logger = get_logger(__name__)

_STORE_PATH = os.getenv("CHUNK_STORE_PATH", "data/chunk_store.json")

_chunk_store: dict[str, RAGChunk] = {}
_chunk_store_loaded = False


def _load_chunk_store() -> None:
    global _chunk_store, _chunk_store_loaded
    if _chunk_store_loaded:
        return
    _chunk_store_loaded = True
    if path_exists(_STORE_PATH):
        try:
            content = read_text(_STORE_PATH)
            if content:
                raw = json.loads(content)
                _chunk_store.update({k: RAGChunk(**v) for k, v in raw.items()})
            logger.info("Loaded %d chunks from %s", len(_chunk_store), _STORE_PATH)
        except Exception:
            logger.exception("Failed to load chunk store from %s", _STORE_PATH)


def _save_chunk_store() -> None:
    try:
        payload = {k: v.model_dump() for k, v in _chunk_store.items()}
        content = json.dumps(payload, ensure_ascii=False)
        if write_text(_STORE_PATH, content):
            logger.info("Saved %d chunks to %s", len(_chunk_store), _STORE_PATH)
    except Exception:
        logger.exception("Failed to save chunk store to %s", _STORE_PATH)


def get_chunk_store() -> dict[str, RAGChunk]:
    _load_chunk_store()
    return _chunk_store


def save_chunk_store() -> None:
    """Persist in-memory chunk store to disk. Safe to call from incremental sync."""
    _save_chunk_store()


class RAGIngestionService:
    def __init__(self) -> None:
        self._gql = GraphQLService()
        self._embedder = VertexEmbeddingService()
        self._vector_store = VertexVectorSearchService()
        self._cache = CacheService()

    async def _process_product(
        self, raw: dict[str, Any]
    ) -> tuple[list[RAGChunk], list[list[float]]]:
        product = normalize_product(raw)
        stock_status = raw.get("stock_status")
        doc = product_to_rag_document(product, stock_status=stock_status)
        full_text = rag_document_to_text(doc)
        chunks = chunk_rag_document(doc, full_text)

        texts = [c.text for c in chunks]
        embeddings = await self._embedder.embed_texts(texts)

        return chunks, embeddings

    async def _store_chunks(
        self,
        chunks: list[RAGChunk],
        embeddings: list[list[float]],
    ) -> None:
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

        for chunk in chunks:
            _chunk_store[chunk.chunk_id] = chunk

    async def sync_all(self) -> RAGSyncResponse:
        logger.info("Starting full RAG sync…")
        all_raw = await self._gql.fetch_all_products(per_page=50)

        total_chunks = 0
        total_embeddings = 0

        batch_chunks: list[RAGChunk] = []
        batch_embeddings: list[list[float]] = []

        for idx, raw in enumerate(all_raw):
            try:
                chunks, embeddings = await self._process_product(raw)
                batch_chunks.extend(chunks)
                batch_embeddings.extend(embeddings)

                if len(batch_chunks) >= 200:
                    await self._store_chunks(batch_chunks, batch_embeddings)
                    total_chunks += len(batch_chunks)
                    total_embeddings += len(batch_embeddings)
                    batch_chunks = []
                    batch_embeddings = []
                    logger.info("Stored batch — total so far: %d chunks", total_chunks)
                    if total_chunks % 1000 < 200:
                        _save_chunk_store()
            except Exception:
                sku = raw.get("sku", "?")
                logger.exception("Failed to process product %s", sku)

        if batch_chunks:
            await self._store_chunks(batch_chunks, batch_embeddings)
            total_chunks += len(batch_chunks)
            total_embeddings += len(batch_embeddings)

        # Rebuild BM25 index after full sync
        try:
            from app.services.hybrid_search_service import rebuild_bm25_index
            rebuild_bm25_index()
        except Exception:
            logger.warning("BM25 index rebuild failed after sync")

        # Invalidate caches
        self._cache.invalidate_all_products()

        _save_chunk_store()

        logger.info(
            "Full sync complete: %d products, %d chunks",
            len(all_raw), total_chunks,
        )

        return RAGSyncResponse(
            products_synced=len(all_raw),
            chunks_created=total_chunks,
            embeddings_generated=total_embeddings,
            index_updated=True,
        )

    async def sync_product(self, sku: str) -> RAGSyncSingleResponse:
        logger.info("Syncing single product: %s", sku)
        raw = await self._gql.fetch_product_by_sku(sku)
        if not raw:
            raise ValueError(f"Product {sku} not found in Magento")

        old_ids = [cid for cid, c in _chunk_store.items() if c.sku == sku]
        if old_ids:
            await self._vector_store.remove_vectors(old_ids)
            for cid in old_ids:
                _chunk_store.pop(cid, None)

        chunks, embeddings = await self._process_product(raw)
        await self._store_chunks(chunks, embeddings)

        # Rebuild BM25 index
        try:
            from app.services.hybrid_search_service import rebuild_bm25_index
            rebuild_bm25_index()
        except Exception:
            logger.warning("BM25 index rebuild failed after single sync")

        self._cache.invalidate_product(sku)
        _save_chunk_store()

        return RAGSyncSingleResponse(
            sku=sku,
            chunks_created=len(chunks),
            embeddings_generated=len(embeddings),
            index_updated=True,
        )

    async def delete_product(self, sku: str) -> RAGDeleteResponse:
        logger.info("Deleting RAG data for product: %s", sku)
        old_ids = [cid for cid, c in _chunk_store.items() if c.sku == sku]
        if old_ids:
            await self._vector_store.remove_vectors(old_ids)
            for cid in old_ids:
                _chunk_store.pop(cid, None)

        self._cache.invalidate_product(sku)
        _save_chunk_store()

        return RAGDeleteResponse(sku=sku, chunks_removed=len(old_ids))
