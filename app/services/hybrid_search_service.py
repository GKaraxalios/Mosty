"""Hybrid search: dense (Vertex AI vectors) + sparse (BM25) + metadata filtering."""
from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import (
    HybridSearchFilters,
    RAGChunk,
    RAGSearchResult,
)
from app.services import es_hybrid
from app.services.cache_service import CacheService, NS_RAG_SEARCH
from app.services.rag_ingestion_service import get_chunk_store
from app.services.vertex_service import (
    VectorSearchUnavailable,
    VertexEmbeddingService,
    VertexVectorSearchService,
    classify_vertex_error,
)
from app.utils.commerce_semantics import (
    category_matches_filter,
    haystack_matches_color,
    haystack_matches_fit,
    haystack_matches_material,
    haystack_matches_occasion,
    haystack_matches_season,
    haystack_matches_style,
    fold_el,
)
from app.utils.brand_resolution import brand_matches_haystack
from app.utils.scoring import BM25Scorer, normalize_scores

logger = get_logger(__name__)

_bm25: BM25Scorer | None = None


def get_bm25_scorer() -> BM25Scorer:
    global _bm25
    if _bm25 is None:
        _bm25 = BM25Scorer()
    return _bm25


def rebuild_bm25_index() -> int:
    """Rebuild the BM25 index from the current chunk store."""
    scorer = get_bm25_scorer()
    store = get_chunk_store()
    chunks = list(store.values())
    scorer.build(chunks)
    logger.info("BM25 index rebuilt with %d documents", scorer.n_docs)
    return scorer.n_docs


def _apply_metadata_filters(
    chunks: dict[str, RAGChunk],
    filters: HybridSearchFilters,
) -> dict[str, RAGChunk]:
    if not any([
        filters.category,
        filters.brand,
        filters.min_price,
        filters.max_price,
        filters.product_type,
        filters.color,
        filters.size,
        filters.occasion,
        filters.style,
        filters.material,
        filters.season,
        filters.fit,
        filters.sale_only,
        filters.new_arrivals_only,
    ]):
        return chunks

    filtered: dict[str, RAGChunk] = {}
    for cid, chunk in chunks.items():
        haystack = " ".join([
            chunk.name or "",
            chunk.text or "",
            " ".join(chunk.categories or []),
            chunk.meta_keyword or "",
            chunk.meta_description or "",
            chunk.description_text or "",
        ]).lower()

        if filters.category:
            if not category_matches_filter(filters.category, chunk.categories or []):
                continue

        if filters.brand:
            if not brand_matches_haystack(filters.brand, haystack):
                continue

        if filters.min_price is not None and chunk.price is not None:
            if chunk.price < filters.min_price:
                continue

        if filters.max_price is not None and chunk.price is not None:
            if chunk.price > filters.max_price:
                continue

        if filters.product_type and fold_el(filters.product_type) not in fold_el(haystack):
            continue

        if filters.color and not haystack_matches_color(haystack, filters.color):
            continue

        if filters.size and fold_el(filters.size) not in fold_el(haystack):
            continue

        if filters.occasion and not haystack_matches_occasion(haystack, filters.occasion):
            continue

        if filters.style and not haystack_matches_style(haystack, filters.style):
            continue

        if filters.material and not haystack_matches_material(haystack, filters.material):
            continue

        if filters.season and not haystack_matches_season(haystack, filters.season):
            continue

        if filters.fit and not haystack_matches_fit(haystack, filters.fit):
            continue

        if filters.sale_only:
            chunk_text_lower = (chunk.text or "").lower()
            has_sale = any(kw in chunk_text_lower or kw in haystack for kw in [
                "sale", "έκπτωση", "προσφορά", "discount", "offer",
            ])
            if not has_sale:
                continue

        if filters.new_arrivals_only and chunk.updated_at:
            from datetime import datetime, timezone, timedelta
            try:
                updated = datetime.fromisoformat(chunk.updated_at.replace("Z", "+00:00"))
                cutoff = datetime.now(timezone.utc) - timedelta(days=30)
                if updated < cutoff:
                    continue
            except (ValueError, TypeError):
                pass

        filtered[cid] = chunk

    return filtered

class HybridSearchService:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._embedder = VertexEmbeddingService()
        self._vector_store = VertexVectorSearchService()
        self._cache = CacheService()

    async def search(
        self,
        query: str,
        top_k: int = 10,
        filters: HybridSearchFilters | None = None,
    ) -> list[RAGSearchResult]:
        filters = filters or HybridSearchFilters()
        dense_weight = self._settings.HYBRID_DENSE_WEIGHT
        sparse_weight = self._settings.HYBRID_SPARSE_WEIGHT

        cache_key = f"{query}:{top_k}:{filters.model_dump_json()}"
        cached = self._cache.get_hashed(NS_RAG_SEARCH, cache_key)
        if cached is not None:
            logger.debug("Hybrid search cache hit for query=%r", query)
            return cached

        chunk_store = get_chunk_store()

        # 1. Metadata pre-filtering
        eligible = _apply_metadata_filters(chunk_store, filters)
        if not eligible:
            logger.warning(
                "hybrid_search zero eligible chunks after metadata filters query=%r filters=%s",
                query[:200],
                filters.model_dump(exclude_none=True),
            )
            return []

        # 1b. ES hybrid (Elasticsearch BM25 + NumPy shards + RRF), when enabled.
        # Any failure (ES down, etc.) falls through to the legacy Vertex + in-memory BM25 path.
        if es_hybrid.enabled():
            try:
                es_results, es_mode = await self._search_es_hybrid(
                    query, top_k, eligible, len(chunk_store)
                )
                if es_mode == "es_hybrid":  # don't cache keyword-only degradation
                    self._cache.set_hashed(
                        NS_RAG_SEARCH, cache_key, es_results,
                        ttl=self._settings.CACHE_RAG_SEARCH_TTL,
                    )
                return es_results
            except Exception:
                logger.exception("es_hybrid search failed; falling back to legacy hybrid path")

        # 2. Dense retrieval — fetch more candidates for reranking.
        # If Vertex (embeddings or Vector Search) is unavailable, continue with BM25 over
        # the same Magento-sourced chunk store; results are flagged as lexical_fallback.
        fetch_n = max(top_k * 3, self._settings.RERANK_TOP_N)
        retrieval_mode = "hybrid"
        try:
            query_embedding = await self._embedder.embed_query(query)
            neighbors = await self._vector_store.find_neighbors(
                query_embedding, top_k=fetch_n
            )
        except Exception as exc:
            if not isinstance(exc, VectorSearchUnavailable):
                exc = VectorSearchUnavailable(
                    classify_vertex_error(exc), str(exc), dependency="vertex_embedding"
                )
            retrieval_mode = "lexical_fallback"
            neighbors = []
            logger.warning(
                "fallback.activated dependency=%s category=%s mode=bm25_chunk_store eligible_n=%d",
                exc.dependency,
                exc.category,
                len(eligible),
            )
            dense_weight, sparse_weight = 0.0, 1.0

        dense_raw: dict[str, float] = {}
        for n in neighbors:
            if n["id"] in eligible:
                dense_raw[n["id"]] = n.get("score", 0.0)
        dense_norm = normalize_scores(dense_raw)

        # 3. Sparse retrieval (BM25)
        scorer = get_bm25_scorer()
        if scorer.n_docs == 0:
            rebuild_bm25_index()
        sparse_raw = scorer.score(query)
        sparse_filtered = {cid: s for cid, s in sparse_raw.items() if cid in eligible}
        sparse_norm = normalize_scores(sparse_filtered)

        # 4. Merge scores
        all_ids = set(dense_norm.keys()) | set(sparse_norm.keys())
        merged: list[tuple[str, float, float, float]] = []
        for cid in all_ids:
            d = dense_norm.get(cid, 0.0)
            s = sparse_norm.get(cid, 0.0)
            final = dense_weight * d + sparse_weight * s
            merged.append((cid, final, d, s))

        merged.sort(key=lambda x: x[1], reverse=True)
        top_results = merged[:top_k]

        results: list[RAGSearchResult] = []
        for cid, final_score, d_score, s_score in top_results:
            chunk = eligible.get(cid)
            if not chunk:
                continue
            results.append(
                RAGSearchResult(
                    score=round(final_score, 4),
                    # No dense score was computed in fallback mode; don't report one.
                    dense_score=round(d_score, 4) if retrieval_mode == "hybrid" else None,
                    sparse_score=round(s_score, 4),
                    sku=chunk.sku,
                    name=chunk.name,
                    chunk_text=chunk.text,
                    url=chunk.url,
                    metadata={
                        "categories": chunk.categories,
                        "price": chunk.price,
                        "updated_at": chunk.updated_at,
                        "source": chunk.source,
                        "stock_status": chunk.stock_status,
                        "image_url": chunk.image_url,
                        "retrieval_mode": retrieval_mode,
                    },
                )
            )

        if not results and eligible:
            logger.warning(
                "hybrid_search no scored results (dense+sparse empty for eligible set) query=%r "
                "eligible_n=%d filters=%s",
                query[:200],
                len(eligible),
                filters.model_dump(exclude_none=True),
            )

        # Don't cache degraded results, so the hybrid path is used as soon as Vertex recovers.
        if retrieval_mode == "hybrid":
            self._cache.set_hashed(
                NS_RAG_SEARCH,
                cache_key,
                results,
                ttl=self._settings.CACHE_RAG_SEARCH_TTL,
            )

        return results

    async def _search_es_hybrid(
        self,
        query: str,
        top_k: int,
        eligible: dict[str, RAGChunk],
        total_chunks: int,
    ) -> tuple[list[RAGSearchResult], str]:
        out = await es_hybrid.search(
            query,
            top_k=top_k,
            eligible_ids=set(eligible.keys()),
            total_chunks=total_chunks,
        )
        mode = "es_hybrid" if not out.get("degraded") else "es_keyword_only"
        if out.get("degraded"):
            logger.warning("es_hybrid degraded: %s", out["degraded"])

        results: list[RAGSearchResult] = []
        for row in out["results"]:
            chunk = eligible.get(str(row.get("chunk_id")))  # also applies metadata filters
            if not chunk:
                continue
            results.append(
                RAGSearchResult(
                    score=round(row.get("fusion_score_norm", 0.0), 4),
                    dense_score=None,   # RRF fuses ranks; per-leg scores are not exposed by the kit
                    sparse_score=None,
                    sku=chunk.sku,
                    name=chunk.name,
                    chunk_text=chunk.text,
                    url=chunk.url,
                    metadata={
                        "categories": chunk.categories,
                        "price": chunk.price,
                        "updated_at": chunk.updated_at,
                        "source": chunk.source,
                        "stock_status": chunk.stock_status,
                        "image_url": chunk.image_url,
                        "retrieval_mode": mode,
                        "match_source": row.get("__src"),
                    },
                )
            )
            if len(results) >= top_k:
                break
        return results, mode
