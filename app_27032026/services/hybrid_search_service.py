"""Hybrid search: dense (Vertex AI vectors) + sparse (BM25) + metadata filtering."""
from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import (
    HybridSearchFilters,
    RAGChunk,
    RAGSearchResult,
)
from app.utils.color_taxonomy import chunk_matches_color, resolve_color_canonical
from app.services.cache_service import CacheService, NS_RAG_SEARCH
from app.services.rag_ingestion_service import get_chunk_store
from app.services.vertex_service import VertexEmbeddingService, VertexVectorSearchService
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


def _ensure_sparse_overlap_eligible(
    sparse_raw: dict[str, float],
    sparse_filtered: dict[str, float],
    eligible: dict[str, RAGChunk],
) -> dict[str, float]:
    """If BM25 keys do not intersect eligible chunk ids (id drift / stale index), use uniform sparse scores."""
    if sparse_filtered or not eligible:
        return sparse_filtered
    missing = [cid for cid in eligible if cid not in sparse_raw]
    if missing:
        logger.warning(
            "BM25/eligible chunk id mismatch: %d eligible id(s) not in BM25 sparse keys "
            "(sparse_raw=%d); applying uniform sparse scores so hybrid can rank",
            len(missing),
            len(sparse_raw),
        )
    return {cid: 1.0 for cid in eligible}


# Greek store labels vs English Magento paths often differ; match via synonym groups.
_CATEGORY_EQUIV_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({
        "φορέματα", "φόρεμα", "φορεμα", "φορέμα",
        "dress", "dresses", "gown", "gowns",
    }),
    frozenset({
        "παντελόνια", "παντελόνι", "παντελονια",
        "pants", "trousers", "jeans", "denim",
    }),
    frozenset({
        "μπλούζες", "μπλούζα", "μπλουζα", "μπλουζες",
        "top", "tops", "blouse", "blouses", "shirt", "shirts", "tee",
    }),
    frozenset({
        "παπούτσια", "παπουτσια",
        "shoes", "sneakers", "boots", "heels", "σανδάλια", "σανδαλια",
    }),
    frozenset({
        "τσάντες", "τσαντες",
        "bag", "bags", "handbag", "purse", "clutch",
    }),
)


def _soft_token_matches_filter(cat_lower: str, token: str) -> bool:
    if not cat_lower or not token:
        return False
    if cat_lower == token:
        return True
    if len(token) < 3:
        return False
    return token in cat_lower or cat_lower in token


def _needles_for_category_filter(cat_lower: str) -> frozenset[str]:
    needles: set[str] = {cat_lower}
    for group in _CATEGORY_EQUIV_GROUPS:
        if any(_soft_token_matches_filter(cat_lower, g) for g in group):
            needles |= group
    return frozenset(needles)


def _category_filter_matches_chunk(
    cat_lower: str,
    chunk_categories: list[str] | None,
    haystack: str,
) -> bool:
    """Match category filter to chunk labels and/or searchable text (bilingual)."""
    cats = chunk_categories or []
    cats_blob = " ".join(c.lower() for c in cats)
    combined = f"{cats_blob} {haystack}"

    for c in cats:
        cl = c.lower()
        if cat_lower in cl or cl in cat_lower:
            return True

    if cat_lower in haystack:
        return True

    for needle in _needles_for_category_filter(cat_lower):
        if len(needle) < 3:
            continue
        if needle in combined:
            return True
    return False


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
        filters.color_canonical,
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
            cat_lower = filters.category.lower().strip()
            if not _category_filter_matches_chunk(cat_lower, chunk.categories, haystack):
                continue

        if filters.brand:
            brand_lower = filters.brand.lower()
            if brand_lower not in haystack:
                continue

        if filters.min_price is not None and chunk.price is not None:
            if chunk.price < filters.min_price:
                continue

        if filters.max_price is not None and chunk.price is not None:
            if chunk.price > filters.max_price:
                continue

        if filters.product_type and filters.product_type.lower() not in haystack:
            continue

        if filters.color_canonical:
            cid = filters.color_canonical
            if not chunk_matches_color(haystack, cid, strict=filters.color_strict):
                continue
        elif filters.color:
            cnorm = resolve_color_canonical(filters.color)
            if cnorm:
                if not chunk_matches_color(haystack, cnorm, strict=filters.color_strict):
                    continue
            elif filters.color.lower() not in haystack:
                continue

        if filters.size and filters.size.lower() not in haystack:
            continue

        if filters.occasion and filters.occasion.lower() not in haystack:
            continue

        if filters.style and filters.style.lower() not in haystack:
            continue

        if filters.material and filters.material.lower() not in haystack:
            continue

        if filters.season and filters.season.lower() not in haystack:
            continue

        if filters.fit and filters.fit.lower() not in haystack:
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


def _bm25_only_results(
    eligible: dict[str, RAGChunk],
    query: str,
    top_k: int,
) -> list[RAGSearchResult]:
    """Sparse-only retrieval when hybrid merge fails (BM25 over eligible chunk ids)."""
    scorer = get_bm25_scorer()
    if scorer.n_docs == 0:
        rebuild_bm25_index()
    sparse_raw = scorer.score(query)
    sparse_filtered = {cid: s for cid, s in sparse_raw.items() if cid in eligible}
    if not sparse_filtered and eligible and sparse_raw:
        rebuild_bm25_index()
        scorer = get_bm25_scorer()
        sparse_raw = scorer.score(query)
        sparse_filtered = {cid: s for cid, s in sparse_raw.items() if cid in eligible}
    sparse_filtered = _ensure_sparse_overlap_eligible(sparse_raw, sparse_filtered, eligible)
    sparse_norm = normalize_scores(sparse_filtered)
    ranked = sorted(
        sparse_norm.items(),
        key=lambda x: x[1],
        reverse=True,
    )[:top_k]
    out: list[RAGSearchResult] = []
    for cid, final_score in ranked:
        chunk = eligible.get(cid)
        if not chunk:
            continue
        out.append(
            RAGSearchResult(
                score=round(final_score, 4),
                dense_score=None,
                sparse_score=round(final_score, 4),
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
                },
            )
        )
    return out


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
        if self._settings.COMMERCE_SEARCH_FILTERS_DISABLED:
            filters = HybridSearchFilters()
        dense_weight = self._settings.HYBRID_DENSE_WEIGHT
        sparse_weight = self._settings.HYBRID_SPARSE_WEIGHT

        cache_key = f"{query}:{top_k}:{filters.model_dump_json()}"
        cached = self._cache.get_hashed(NS_RAG_SEARCH, cache_key)
        if cached is not None:
            if cached:
                logger.debug("Hybrid search cache hit for query=%r", query)
                return cached
            # Stale empty entries were cached before BM25/skip-cache fixes — recompute if we have data
            _store = get_chunk_store()
            if not _store:
                logger.debug("Hybrid search cache hit (empty, no chunks)")
                return cached
            logger.warning(
                "Hybrid search: ignoring cached empty result (chunk_store has %d chunks); recomputing",
                len(_store),
            )

        chunk_store = get_chunk_store()

        # 1. Metadata pre-filtering
        eligible = _apply_metadata_filters(chunk_store, filters)
        skip_full_catalog = (
            bool(filters.category and str(filters.category).strip())
            and getattr(
                self._settings,
                "HYBRID_SKIP_FULL_CATALOG_FALLBACK_WHEN_CATEGORY_FILTER",
                True,
            )
        )
        if not eligible and chunk_store and self._settings.HYBRID_FALLBACK_WHEN_NO_ELIGIBLE:
            if skip_full_catalog:
                logger.warning(
                    "Hybrid search: zero eligible chunks with category filter; "
                    "not using full-catalog fallback (keeps category constraint). filters=%s",
                    filters.model_dump_json()[:300],
                )
            else:
                logger.warning(
                    "Hybrid search: zero eligible chunks after metadata filters; "
                    "falling back to full catalog (query still ranks via dense+sparse). filters=%s",
                    filters.model_dump_json()[:300],
                )
                eligible = dict(chunk_store)
                cache_key = f"{cache_key}::full_catalog_fallback"

        if not eligible:
            return []

        try:
            return await self._hybrid_search_eligible(
                eligible=eligible,
                query=query,
                top_k=top_k,
                dense_weight=dense_weight,
                sparse_weight=sparse_weight,
                cache_key=cache_key,
            )
        except Exception as exc:
            logger.exception(
                "Hybrid search failed; BM25-only recovery: %s",
                exc,
            )
            results = _bm25_only_results(eligible, query, top_k)
            self._cache.set_hashed(
                NS_RAG_SEARCH,
                cache_key,
                results,
                ttl=self._settings.CACHE_RAG_SEARCH_TTL,
            )
            return results

    async def _hybrid_search_eligible(
        self,
        *,
        eligible: dict[str, RAGChunk],
        query: str,
        top_k: int,
        dense_weight: float,
        sparse_weight: float,
        cache_key: str,
    ) -> list[RAGSearchResult]:
        # 2. Dense retrieval — fetch enough neighbors so ∩ eligible stays high after strict filters
        fetch_n = max(
            top_k * 4,
            self._settings.RERANK_TOP_N,
            self._settings.HYBRID_DENSE_NEIGHBOR_MIN,
        )
        dense_raw: dict[str, float] = {}
        try:
            query_embedding = await self._embedder.embed_query(query)
            neighbors = await self._vector_store.find_neighbors(
                query_embedding, top_k=fetch_n
            )
            for n in neighbors:
                if n["id"] in eligible:
                    dense_raw[n["id"]] = n.get("score", 0.0)
        except Exception as exc:
            logger.exception(
                "Dense vector retrieval failed; continuing with BM25-only: %s",
                exc,
            )
        dense_norm = normalize_scores(dense_raw)

        # 3. Sparse retrieval (BM25)
        scorer = get_bm25_scorer()
        if scorer.n_docs == 0:
            rebuild_bm25_index()
        sparse_raw = scorer.score(query)
        sparse_filtered = {cid: s for cid, s in sparse_raw.items() if cid in eligible}
        # Stale BM25 index (chunk store grew / ids changed) → sparse keys ∩ eligible = ∅
        if not sparse_filtered and eligible:
            if not sparse_raw:
                logger.warning(
                    "BM25 returned no scores (n_docs may be 0); rebuilding index from chunk store"
                )
                rebuild_bm25_index()
                scorer = get_bm25_scorer()
                sparse_raw = scorer.score(query)
                sparse_filtered = {cid: s for cid, s in sparse_raw.items() if cid in eligible}
            if not sparse_filtered and sparse_raw:
                logger.warning(
                    "BM25 sparse_filtered empty but sparse_raw has %d keys vs %d eligible (rebuilding BM25)",
                    len(sparse_raw),
                    len(eligible),
                )
                rebuild_bm25_index()
                scorer = get_bm25_scorer()
                sparse_raw = scorer.score(query)
                sparse_filtered = {cid: s for cid, s in sparse_raw.items() if cid in eligible}
        sparse_filtered = _ensure_sparse_overlap_eligible(sparse_raw, sparse_filtered, eligible)
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

        # Dense missing (Vector Search off) + merge edge case: recover with BM25-only
        if not top_results and eligible:
            logger.warning(
                "Hybrid merge produced no candidates (dense=%s sparse_filtered=%s); BM25-only recovery",
                len(dense_norm),
                len(sparse_filtered),
            )
            recovery = _bm25_only_results(eligible, query, top_k)
            if recovery:
                self._cache.set_hashed(
                    NS_RAG_SEARCH,
                    cache_key,
                    recovery,
                    ttl=self._settings.CACHE_RAG_SEARCH_TTL,
                )
                return recovery

        results: list[RAGSearchResult] = []
        for cid, final_score, d_score, s_score in top_results:
            chunk = eligible.get(cid)
            if not chunk:
                continue
            results.append(
                RAGSearchResult(
                    score=round(final_score, 4),
                    dense_score=round(d_score, 4),
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
                    },
                )
            )

        if not results and eligible:
            logger.warning(
                "Skipping cache: empty hybrid results with %d eligible chunks (query=%r)",
                len(eligible),
                query[:120],
            )
        else:
            self._cache.set_hashed(
                NS_RAG_SEARCH,
                cache_key,
                results,
                ttl=self._settings.CACHE_RAG_SEARCH_TTL,
            )

        return results
