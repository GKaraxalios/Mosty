"""RAG query service — now with hybrid search, reranking, caching, and multilingual support."""
from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import (
    HybridSearchFilters,
    RAGAskResponse,
    RAGSearchResponse,
    RAGSearchResult,
    RAGSourceCitation,
)
from app.services.cache_service import CacheService, NS_RAG_ANSWER, NS_RAG_SEARCH
from app.services.hybrid_search_service import HybridSearchService
from app.services.rerank_service import maybe_rerank
from app.services.vertex_service import VertexGenerativeService
from app.utils.language import detect_language
from app.utils.prompt_builder import (
    build_no_results_response,
    build_rag_prompt,
    get_system_instruction,
)

logger = get_logger(__name__)


class RAGQueryService:
    def __init__(self) -> None:
        self._hybrid = HybridSearchService()
        self._generator = VertexGenerativeService()
        self._settings = get_settings()
        self._cache = CacheService()

    async def search(
        self,
        query: str,
        top_k: int = 5,
        filters: HybridSearchFilters | None = None,
    ) -> RAGSearchResponse:
        logger.info("RAG search: query=%r, top_k=%d", query, top_k)

        raw_results = await self._hybrid.search(
            query=query,
            top_k=self._settings.RERANK_TOP_N,
            filters=filters,
        )
        results = maybe_rerank(query, raw_results)
        results = results[:top_k]

        return RAGSearchResponse(query=query, results=results)

    async def ask(
        self,
        query: str,
        top_k: int = 5,
        temperature: float = 0.2,
    ) -> RAGAskResponse:
        logger.info("RAG ask: query=%r, top_k=%d, temperature=%.2f", query, top_k, temperature)

        lang = detect_language(query)

        search_response = await self.search(query, top_k=top_k)

        if not search_response.results:
            return RAGAskResponse(
                query=query,
                answer=build_no_results_response(lang),
                language=lang,
                sources=[],
            )

        prompt = build_rag_prompt(query, search_response.results, lang)

        cache_key = f"{prompt}:{temperature}"
        cached = self._cache.get_hashed(NS_RAG_ANSWER, cache_key)
        if cached:
            answer = cached
        else:
            answer = await self._generator.generate(
                prompt=prompt,
                system_instruction=get_system_instruction(lang),
                temperature=temperature,
            )
            self._cache.set_hashed(
                NS_RAG_ANSWER, cache_key, answer,
                ttl=self._settings.CACHE_RAG_ANSWER_TTL,
            )

        seen_skus: set[str] = set()
        sources: list[RAGSourceCitation] = []
        for r in search_response.results:
            if r.sku not in seen_skus:
                seen_skus.add(r.sku)
                sources.append(
                    RAGSourceCitation(
                        sku=r.sku, name=r.name, url=r.url, score=r.score,
                    )
                )

        return RAGAskResponse(
            query=query,
            answer=answer,
            language=lang,
            sources=sources,
        )
