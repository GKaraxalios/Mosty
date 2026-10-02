"""Pluggable reranking layer.

Provides a base interface and two implementations:
- HeuristicReranker: rule-based scoring boosts
- LLMReranker: placeholder for LLM-based or cross-encoder reranking
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import RAGSearchResult
from app.utils.scoring import tokenize

logger = get_logger(__name__)


class BaseReranker(ABC):
    @abstractmethod
    def rerank(
        self,
        query: str,
        results: list[RAGSearchResult],
        top_n: int = 5,
    ) -> list[RAGSearchResult]: ...


class HeuristicReranker(BaseReranker):
    """Rule-based reranker that boosts results based on keyword/metadata signals."""

    def rerank(
        self,
        query: str,
        results: list[RAGSearchResult],
        top_n: int = 5,
    ) -> list[RAGSearchResult]:
        if not results:
            return results

        merch_boost = _get_merchandising_boosts()
        query_tokens = set(tokenize(query))
        scored: list[tuple[RAGSearchResult, float]] = []

        for r in results:
            m_boost = merch_boost(r.sku, r.metadata.get("categories", []), r.name)
            if m_boost == float("-inf"):
                continue

            boost = m_boost

            title_tokens = set(tokenize(r.name))
            overlap = query_tokens & title_tokens
            if overlap:
                boost += 0.15 * (len(overlap) / max(len(query_tokens), 1))

            sku_lower = r.sku.lower()
            for qt in query_tokens:
                if qt in sku_lower:
                    boost += 0.10
                    break

            cats = r.metadata.get("categories", [])
            if cats:
                cat_tokens = set()
                for c in cats:
                    cat_tokens.update(tokenize(c))
                cat_overlap = query_tokens & cat_tokens
                if cat_overlap:
                    boost += 0.08

            chunk_tokens = set(tokenize(r.chunk_text[:500]))
            desc_overlap = query_tokens & chunk_tokens
            if desc_overlap:
                boost += 0.05 * (len(desc_overlap) / max(len(query_tokens), 1))

            if r.metadata.get("price"):
                boost += 0.02

            rerank_score = r.score + boost
            r_copy = r.model_copy(update={"rerank_score": round(rerank_score, 4)})
            scored.append((r_copy, rerank_score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [item for item, _ in scored[:top_n]]


class LLMReranker(BaseReranker):
    """Placeholder for LLM/cross-encoder based reranking.

    Replace with Vertex AI reranking endpoint or a cross-encoder model
    when available.
    """

    def rerank(
        self,
        query: str,
        results: list[RAGSearchResult],
        top_n: int = 5,
    ) -> list[RAGSearchResult]:
        logger.info("LLMReranker called — falling back to HeuristicReranker")
        return HeuristicReranker().rerank(query, results, top_n)


def _get_merchandising_boosts():
    """Return a callable that computes merchandising boost for a result.

    Lazily imports MerchandisingService to avoid circular imports and
    gracefully returns zero-boost if the service is not available yet.
    """
    try:
        from app.services.merchandising_service import MerchandisingService
        svc = MerchandisingService()
        return svc.compute_boost
    except Exception:
        return lambda sku, cats, name: 0.0


def get_reranker() -> BaseReranker:
    """Factory: return the configured reranker."""
    return HeuristicReranker()


def maybe_rerank(
    query: str,
    results: list[RAGSearchResult],
    top_k: int | None = None,
) -> list[RAGSearchResult]:
    """Apply reranking if enabled in settings. Use top_k if provided, else FINAL_TOP_K."""
    settings = get_settings()
    n = top_k if top_k is not None else settings.FINAL_TOP_K
    n = min(max(1, n), 20)
    if not settings.RERANK_ENABLED:
        return results[:n]
    reranker = get_reranker()
    return reranker.rerank(query, results, top_n=n)
