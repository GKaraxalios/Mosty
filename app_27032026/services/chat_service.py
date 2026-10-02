"""Chat service: orchestrates intent extraction + smart search + styling generation.

This is the high-level service that powers the /chat/* endpoints.
Implements the ELIZA AI AGENT Stylist architecture with fashion-aware intent
extraction, retrieval, and stylist-quality response generation.
Supports weather-aware outfit recommendations when location is provided.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import (
    ChatCompareRequest,
    ChatCompareResponse,
    ChatRecommendRequest,
    ChatRecommendResponse,
    ChatRequest,
    ChatResponse,
    HybridSearchFilters,
    ProductCard,
    RAGSearchResult,
    RAGSourceCitation,
)
from app.services.cache_service import CacheService
from app.services.hybrid_search_service import HybridSearchService
from app.services.memory_service import get_memory_service
from app.services.rag_ingestion_service import get_chunk_store
from app.services.rerank_service import maybe_rerank
from app.services.vertex_service import VertexGenerativeService
from app.services.weather_service import get_weather_by_city, get_weather_by_coords
from app.utils.intent import (
    FashionIntent,
    build_intent_prompt,
    parse_intent,
    should_retrieve_products,
)
from app.utils.language import resolve_language
from app.utils.prompt_builder import (
    build_comparison_prompt,
    build_info_only_prompt,
    build_no_results_response,
    build_query_rewrite_prompt,
    build_recommendation_prompt,
    build_stylist_prompt,
    filter_valid_results,
    format_weather_context,
    get_system_instruction,
)

logger = get_logger(__name__)


# Cities we try to extract from the user message when client doesn't send location.
_CITIES_IN_MESSAGE = [
    "Θεσσαλονίκη",
    "Thessaloniki",
    "Ηράκλειο",
    "Ρέθυμνο",
    "Καλαμάτα",
    "Ιωάννινα",
    "Κέρκυρα",
    "Αθήνα",
    "Athens",
    "Πάτρα",
    "Λάρισα",
    "Βόλος",
    "Χανιά",
]


def normalize_greek_commerce_query(text: str) -> str:
    """Very light normalization for common Greek commerce typos."""
    if not text:
        return text
    normalized = text
    replacements = {
        "μάυρ": "μαύρ",
        "μαυρα": "μαύρα",
        "μαυρο": "μαύρο",
        "ασπρο": "άσπρο",
        "φορεμα ": "φόρεμα ",
        "φορεματα": "φορέματα",
    }
    lower_text = normalized.lower()
    for wrong, correct in replacements.items():
        if wrong in lower_text:
            normalized = re.sub(wrong, correct, normalized, flags=re.IGNORECASE)
            lower_text = normalized.lower()
    return normalized


def _extract_city_from_message(text: str) -> str | None:
    """Extract a known city name from the user message for weather lookup."""
    if not text or not text.strip():
        return None
    normalized = f" {text.strip().lower()} "
    for city in _CITIES_IN_MESSAGE:
        needle = f" {city.lower()} "
        if needle in normalized:
            return city
        if normalized.startswith(needle) or normalized.endswith(needle):
            return city
    return None


def _dedupe_sources(results: list[RAGSearchResult]) -> list[RAGSourceCitation]:
    seen: set[str] = set()
    sources: list[RAGSourceCitation] = []
    for r in results:
        if r.sku not in seen:
            seen.add(r.sku)
            sources.append(
                RAGSourceCitation(sku=r.sku, name=r.name, url=r.url, score=r.score)
            )
    return sources


def _dedupe_by_sku(results: list[RAGSearchResult]) -> list[RAGSearchResult]:
    """Keep only the highest-scoring result per unique SKU."""
    best: dict[str, RAGSearchResult] = {}
    for r in results:
        if r.sku not in best or r.score > best[r.sku].score:
            best[r.sku] = r
    return sorted(best.values(), key=lambda x: x.score, reverse=True)


STYLING_FALLBACKS_EL = [
    "Μια κομψή επιλογή που συνδυάζεται εύκολα με διαφορετικά looks.",
    "Ιδανική πρόταση για ένα προσεγμένο και σύγχρονο outfit.",
    "Ένα ευέλικτο κομμάτι που μπορεί να φορεθεί εύκολα σε πολλές εμφανίσεις.",
    "Καθαρές γραμμές και πολυτέλεια για καθημερινό στυλ.",
    "Ταιριάζει ιδανικά σε casual και πιο dressy περιστάσεις.",
]


def _normalize_styling_reason(val: str | None) -> str:
    if val is None:
        return ""
    s = (val if isinstance(val, str) else str(val)).strip()
    if not s or s.lower() in ("null", "undefined"):
        return ""
    return s


def _styling_fallback(sku: str) -> str:
    idx = hash(sku) % len(STYLING_FALLBACKS_EL)
    return STYLING_FALLBACKS_EL[idx]


def _build_product_cards(
    results: list[RAGSearchResult],
    styling_map: dict[str, str] | None = None,
    popularity_map: dict[str, str] | None = None,
    badge_map: dict[str, str] | None = None,
    use_popularity: bool = False,
    match_note: str = "",
) -> list[ProductCard]:
    cards: list[ProductCard] = []
    seen: set[str] = set()

    for r in results:
        if r.sku in seen:
            continue
        seen.add(r.sku)

        meta = r.metadata or {}
        price_val = meta.get("price")
        price_str = f"{price_val}" if price_val else None
        stock = meta.get("stock_status")
        availability = "In stock"
        if stock and stock != "IN_STOCK":
            availability = "Out of stock"

        raw_styling = (styling_map or {}).get(r.sku, "")
        popularity_reason = (popularity_map or {}).get(r.sku, "").strip()
        badge = (badge_map or {}).get(r.sku, "").strip()

        if use_popularity:
            if not popularity_reason:
                popularity_reason = (
                    "Επιλογή που ξεχωρίζει στο MOSTY"
                    if not badge else f"{badge} στο MOSTY"
                )
            if not badge:
                badge = "Featured"
            final_styling = ""
        else:
            normalized = _normalize_styling_reason(raw_styling)
            final_styling = normalized or _styling_fallback(r.sku)
            popularity_reason = ""
            badge = ""

        cards.append(
            ProductCard(
                sku=r.sku,
                name=r.name,
                image=meta.get("image_url"),
                price=price_str,
                currency="EUR",
                availability=availability,
                url=r.url,
                styling_reason=final_styling,
                popularity_reason=popularity_reason,
                badge=badge,
                match_note=match_note,
            )
        )

    return cards


_JSON_RE = re.compile(r"\{[\s\S]*\}", re.DOTALL)


def _serialize_assistant_turn(
    text: str,
    sources: list[RAGSourceCitation] | None = None,
    products: list[ProductCard] | None = None,
    intent: str | None = None,
) -> str:
    payload = {
        "v": 1,
        "text": text,
        "sources": [s.model_dump() for s in (sources or [])],
        "products": [p.model_dump() for p in (products or [])],
        "intent": intent,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _sanitize_sku_maps(
    styling_map: dict[str, str],
    popularity_map: dict[str, str],
    badge_map: dict[str, str],
    valid_skus: set[str],
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    return (
        {k: v for k, v in styling_map.items() if k in valid_skus},
        {k: v for k, v in popularity_map.items() if k in valid_skus},
        {k: v for k, v in badge_map.items() if k in valid_skus},
    )


def _parse_stylist_json(
    raw: str,
) -> tuple[str, dict[str, str], bool, dict[str, str], dict[str, str]]:
    """
    Returns:
    (message, styling_map, llm_returned_no_products, popularity_map, badge_map)
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)

    m = _JSON_RE.search(text)
    if not m:
        return raw, {}, False, {}, {}

    try:
        data = json.loads(m.group())
    except json.JSONDecodeError:
        return raw, {}, False, {}, {}

    message = data.get("message", "")
    products = data.get("products", [])
    no_results = data.get("no_results", False)
    llm_returned_no_products = no_results or (
        isinstance(products, list) and len(products) == 0
    )

    styling_map: dict[str, str] = {}
    popularity_map: dict[str, str] = {}
    badge_map: dict[str, str] = {}

    for p in products:
        if not isinstance(p, dict) or "sku" not in p:
            continue
        sku = p["sku"]

        raw_reason = p.get("styling_reason", "") or ""
        styling_map[sku] = raw_reason if isinstance(raw_reason, str) else str(raw_reason)

        pr = p.get("popularity_reason", "") or ""
        popularity_map[sku] = pr if isinstance(pr, str) else str(pr)

        badge = p.get("badge", "") or ""
        badge_map[sku] = badge if isinstance(badge, str) else str(badge)

    return message, styling_map, llm_returned_no_products, popularity_map, badge_map


def _maybe_print_full_llm_prompt(
    enabled: bool,
    *,
    label: str,
    system_instruction: str,
    user_prompt: str,
) -> None:
    if not enabled:
        return
    print(
        f"\n========== LLM PROMPT ({label}) LOG_STYLIST_PROMPT=true ==========\n"
        f"--- SYSTEM ---\n{system_instruction}\n"
        f"--- USER ---\n{user_prompt}\n"
        "========== END PROMPT ==========\n",
        flush=True,
    )


class ChatService:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._search = HybridSearchService()
        self._generator = VertexGenerativeService()
        self._memory = get_memory_service()
        self._cache = CacheService()

    async def _extract_intent(
        self,
        query: str,
        conversation_context: str,
        lang: str,
    ) -> FashionIntent:
        """Use the LLM to parse the user's fashion shopping intent."""
        prompt = build_intent_prompt(query, conversation_context, lang)
        try:
            raw = await self._generator.generate(
                prompt=prompt,
                system_instruction=(
                    "You are a fashion intent parser. "
                    "Output ONLY valid JSON, nothing else."
                ),
                temperature=0.0,
                max_output_tokens=500,
            )
            return parse_intent(raw, query)
        except Exception:
            logger.warning("Intent extraction failed, using fallback")
            return FashionIntent(search_query=query)

    async def _find_reference_product(self, name: str) -> RAGSearchResult | None:
        """Find a product mentioned by name in the catalog."""
        raw = await self._search.search(name, top_k=5)
        results = maybe_rerank(name, raw)
        if not results:
            return None

        name_lower = name.lower()
        for r in results:
            if name_lower in r.name.lower():
                logger.info("Reference product matched: %s", r.name)
                return r

        logger.info("Reference product closest match: %s", results[0].name)
        return results[0]

    async def _maybe_rewrite_query(
        self,
        query: str,
        session_id: str,
        lang: str,
    ) -> tuple[str, str | None]:
        """Rewrite follow-up queries using conversation context."""
        context = self._memory.get_conversation_context(session_id)
        if not context:
            return query, None

        prompt = build_query_rewrite_prompt(query, context, lang)
        try:
            rewritten = await self._generator.generate(
                prompt=prompt,
                system_instruction="You are a query rewriter. Output only the rewritten query.",
                temperature=0.0,
                max_output_tokens=256,
            )
            rewritten = rewritten.strip().strip('"').strip("'")
            if rewritten and len(rewritten) > 3:
                logger.info("Query rewritten: %r → %r", query, rewritten)
                return rewritten, rewritten
        except Exception:
            logger.warning("Query rewrite failed, using original query")
        return query, None

    def _user_query_for_llm(self, request: ChatRequest) -> str:
        """Augment query with attachment hints for the model (user turn stays raw)."""
        ids = list(request.attachment_ids or [])
        if not ids:
            return request.query

        from app.services.attachment_service import get_attachment_service

        desc = get_attachment_service().describe(ids)
        if not desc:
            return request.query

        lines = "\n".join(f"[Attached file: {d['filename']}]" for d in desc)
        return lines + "\n\n" + request.query

    async def _search_products_with_intent(
        self,
        request: ChatRequest,
        intent: FashionIntent,
        session,
    ) -> tuple[list[RAGSearchResult], HybridSearchFilters]:
        """
        PROD-like retrieval flow:
        - apply session budget
        - apply target category if present
        - run one main search
        - dedupe + valid filter
        - if empty and category exists, retry once without the category filter
        """
        filters = request.filters.model_copy()
        prefs = session.user_preferences

        if prefs.budget_min and not filters.min_price:
            filters.min_price = prefs.budget_min
        if prefs.budget_max and not filters.max_price:
            filters.max_price = prefs.budget_max

        if intent.target_category and not filters.category:
            filters.category = intent.target_category

        raw_results = await self._search.search(
            query=intent.search_query,
            top_k=self._settings.RERANK_TOP_N,
            filters=filters,
        )
        results = maybe_rerank(intent.search_query, raw_results, top_k=request.top_k)
        results = _dedupe_by_sku(results)
        results = filter_valid_results(results)

        # PROD fallback: retry once without category only if empty
        if not results and intent.target_category:
            logger.info(
                "No results for category=%r, retrying without category filter",
                intent.target_category,
            )

            fallback_filters = request.filters.model_copy()

            if prefs.budget_min and not fallback_filters.min_price:
                fallback_filters.min_price = prefs.budget_min
            if prefs.budget_max and not fallback_filters.max_price:
                fallback_filters.max_price = prefs.budget_max

            raw_results = await self._search.search(
                query=intent.search_query,
                top_k=self._settings.RERANK_TOP_N,
                filters=fallback_filters,
            )
            results = maybe_rerank(intent.search_query, raw_results, top_k=request.top_k)
            results = _dedupe_by_sku(results)
            results = filter_valid_results(results)

            return results, fallback_filters

        return results, filters

    async def ask(self, request: ChatRequest) -> ChatResponse:
        lang = resolve_language(request.language, request.query)
        q_llm = self._user_query_for_llm(request)
        session = self._memory.get_or_create(request.session_id, language=lang)

        context = self._memory.get_conversation_context(session.session_id)

        # Step 1: Extract fashion intent
        normalized_query = normalize_greek_commerce_query(q_llm)
        intent = await self._extract_intent(normalized_query, context, lang)

        # Gate: support/store intents → info-only answer, no product retrieval
        if not should_retrieve_products(intent.intent_type):
            logger.info(
                "Info-only intent type=%s, skipping product retrieval",
                intent.intent_type,
            )
            self._memory.add_turn(session.session_id, "user", request.query)
            system_instruction = get_system_instruction(lang)
            prompt = build_info_only_prompt(q_llm, lang)

            # _maybe_print_full_llm_prompt(
            #     self._settings.LOG_STYLIST_PROMPT,
            #     label="info_only",
            #     system_instruction=system_instruction,
            #     user_prompt=prompt,
            # )

            answer = await self._generator.generate(
                prompt=prompt,
                system_instruction=system_instruction,
                temperature=request.temperature,
            )

            if not (answer or "").strip():
                answer = (
                    "Δεν μπόρεσα να παράξω απάντηση αυτή τη στιγμή. Δοκίμασε ξανά ή διατύπωσε διαφορετικά."
                    if lang == "el"
                    else "I couldn't produce an answer right now. Please try again or rephrase your question."
                )

            self._memory.add_turn(
                session.session_id,
                "assistant",
                _serialize_assistant_turn(
                    answer,
                    sources=[],
                    products=[],
                    intent=intent.intent_type,
                ),
            )

            return ChatResponse(
                session_id=session.session_id,
                query=request.query,
                rewritten_query=None,
                answer=answer,
                language=lang,
                sources=[],
                products=[],
                intent=intent.intent_type,
            )

        # Step 2: Find reference product if the user mentioned one by name
        ref_product: RAGSearchResult | None = None
        if intent.reference_product:
            ref_product = await self._find_reference_product(intent.reference_product)

        # Step 3: Search with prod-like intent handling
        results, merged_filters = await self._search_products_with_intent(
            request, intent, session
        )

        rewritten = intent.search_query if intent.search_query != request.query else None

        # Step 4: Optional weather for outfit recommendations
        weather_context = ""
        city_for_weather = (
            (request.location_city or "").strip()
            or _extract_city_from_message(request.query)
        )

        try:
            if request.location_lat is not None and request.location_lon is not None:
                weather = await asyncio.to_thread(
                    get_weather_by_coords,
                    request.location_lat,
                    request.location_lon,
                )
            elif city_for_weather:
                weather = await asyncio.to_thread(
                    get_weather_by_city,
                    city_for_weather,
                )
            else:
                weather = None

            if weather:
                weather_context = format_weather_context(weather, lang)
        except Exception as exc:
            logger.warning("Weather fetch failed: %s", exc)

        retrieval_meta: dict[str, Any] = {}
        if intent.target_category:
            retrieval_meta["target_category"] = intent.target_category
        if merged_filters.category:
            retrieval_meta["applied_category"] = merged_filters.category
        retrieval_meta["result_count"] = len(results)

        # Step 5: Build stylist prompt
        prompt = build_stylist_prompt(
            query=q_llm,
            intent=intent,
            results=results,
            lang=lang,
            conversation_context=context,
            ref_product=ref_product,
            weather_context=weather_context,
            retrieval_meta=retrieval_meta,
        )
        system_instruction = get_system_instruction(lang, output_mode="json")

        # _maybe_print_full_llm_prompt(
        #     self._settings.LOG_STYLIST_PROMPT,
        #     label="stylist_ask",
        #     system_instruction=system_instruction,
        #     user_prompt=prompt,
        # )

        self._memory.add_turn(session.session_id, "user", request.query)

        # Step 6: Generate structured stylist answer
        raw_answer = await self._generator.generate(
            prompt=prompt,
            system_instruction=system_instruction,
            temperature=request.temperature,
            max_output_tokens=4096,
        )

        if not (raw_answer or "").strip() and results:
            raw_answer = (
                "Δεν μπόρεσα να παράξω πλήρη απάντηση αυτή τη στιγμή. Δες τις προτάσεις παρακάτω."
                if lang == "el"
                else "I couldn't generate a full answer right now. See the recommendations below."
            )

        # Step 7: Parse structured JSON and build product cards
        message, styling_map, llm_returned_no_products, popularity_map, badge_map = _parse_stylist_json(raw_answer)
        is_popular_intent = (intent.intent_type or "").lower() == "popular_products"

        valid_skus = {r.sku for r in results}
        styling_map, popularity_map, badge_map = _sanitize_sku_maps(
            styling_map,
            popularity_map,
            badge_map,
            valid_skus,
        )

        if llm_returned_no_products and message and not results:
            self._memory.add_turn(
                session.session_id,
                "assistant",
                _serialize_assistant_turn(
                    message,
                    sources=[],
                    products=[],
                    intent=intent.intent_type,
                ),
            )
            return ChatResponse(
                session_id=session.session_id,
                query=request.query,
                rewritten_query=rewritten,
                answer=message,
                language=lang,
                sources=[],
                products=[],
                intent=intent.intent_type,
            )

        if styling_map or popularity_map:
            ordered_results: list[RAGSearchResult] = []
            remaining = list(results)
            order_skus = list(styling_map) if styling_map else list(popularity_map)

            for sku in order_skus:
                for r in remaining:
                    if r.sku == sku:
                        ordered_results.append(r)
                        remaining.remove(r)
                        break

            ordered_results.extend(remaining)

            product_cards = _build_product_cards(
                ordered_results,
                styling_map=styling_map or None,
                popularity_map=popularity_map or None,
                badge_map=badge_map or None,
                use_popularity=is_popular_intent,
                match_note="",
            )
        else:
            product_cards = _build_product_cards(
                results,
                popularity_map=popularity_map or None,
                badge_map=badge_map or None,
                use_popularity=is_popular_intent,
                match_note="",
            )

        answer = (message or "").strip() or raw_answer

        self._memory.add_turn(
            session.session_id,
            "assistant",
            _serialize_assistant_turn(
                answer,
                sources=_dedupe_sources(results),
                products=product_cards,
                intent=intent.intent_type,
            ),
        )
        self._memory.add_retrieved_skus(session.session_id, [r.sku for r in results])
        self._memory.update_session(
            session.session_id,
            last_query=request.query,
            last_answer=answer,
            language=lang,
        )

        response_metadata: dict[str, Any] | None = None
        if is_popular_intent:
            response_metadata = {"ranking_source": "curated"}
        if intent.target_category:
            response_metadata = response_metadata or {}
            response_metadata["target_category"] = intent.target_category
        if retrieval_meta:
            response_metadata = response_metadata or {}
            response_metadata["retrieval"] = retrieval_meta

        return ChatResponse(
            session_id=session.session_id,
            query=request.query,
            rewritten_query=rewritten,
            answer=answer,
            language=lang,
            sources=_dedupe_sources(results),
            products=product_cards,
            intent=intent.intent_type,
            metadata=response_metadata,
        )

    async def search(self, request: ChatRequest) -> list[RAGSearchResult]:
        lang = resolve_language(request.language, request.query)
        session = self._memory.get_or_create(request.session_id, language=lang)

        search_query, _ = await self._maybe_rewrite_query(
            request.query,
            session.session_id,
            lang,
        )

        raw_results = await self._search.search(
            query=search_query,
            top_k=self._settings.RERANK_TOP_N,
            filters=request.filters,
        )
        return maybe_rerank(search_query, raw_results)

    async def recommend(self, request: ChatRecommendRequest) -> ChatRecommendResponse:
        lang = resolve_language(request.language, request.query)
        session = self._memory.get_or_create(request.session_id, language=lang)

        search_query, _ = await self._maybe_rewrite_query(
            request.query,
            session.session_id,
            lang,
        )

        raw_results = await self._search.search(
            query=search_query,
            top_k=self._settings.RERANK_TOP_N,
            filters=request.filters,
        )
        results = maybe_rerank(search_query, raw_results, top_k=request.top_k)
        results = _dedupe_by_sku(results)
        results = filter_valid_results(results)

        if not results:
            self._memory.add_turn(session.session_id, "user", request.query)
            answer = build_no_results_response(lang)
            self._memory.add_turn(
                session.session_id,
                "assistant",
                _serialize_assistant_turn(answer, sources=[], products=[], intent="recommend"),
            )
            return ChatRecommendResponse(
                session_id=session.session_id,
                query=request.query,
                answer=answer,
                language=lang,
                recommendations=[],
            )

        context = self._memory.get_conversation_context(session.session_id)
        prompt = build_recommendation_prompt(search_query, results, lang, context)

        self._memory.add_turn(session.session_id, "user", request.query)

        answer = await self._generator.generate(
            prompt=prompt,
            system_instruction=get_system_instruction(lang),
            temperature=request.temperature,
        )

        self._memory.add_turn(
            session.session_id,
            "assistant",
            _serialize_assistant_turn(
                answer,
                sources=_dedupe_sources(results),
                products=[],
                intent="recommend",
            ),
        )
        self._memory.add_retrieved_skus(session.session_id, [r.sku for r in results])

        return ChatRecommendResponse(
            session_id=session.session_id,
            query=request.query,
            answer=answer,
            language=lang,
            recommendations=_dedupe_sources(results),
        )

    async def compare(self, request: ChatCompareRequest) -> ChatCompareResponse:
        lang = resolve_language(request.language, request.query or "")

        chunk_store = get_chunk_store()
        product_texts: list[str] = []
        found_sources: list[RAGSourceCitation] = []

        for sku in request.skus:
            chunks = [c for c in chunk_store.values() if c.sku == sku]
            if chunks:
                text = chunks[0].text
                product_texts.append(text)
                found_sources.append(
                    RAGSourceCitation(
                        sku=sku,
                        name=chunks[0].name,
                        url=chunks[0].url,
                        score=1.0,
                    )
                )

        if len(product_texts) < 2:
            no_msg = (
                "Χρειάζονται τουλάχιστον 2 προϊόντα για σύγκριση."
                if lang == "el"
                else "At least 2 products are required for comparison."
            )
            return ChatCompareResponse(
                session_id=request.session_id,
                comparison=no_msg,
                language=lang,
                products=found_sources,
            )

        prompt = build_comparison_prompt(product_texts, request.query, lang)
        comparison = await self._generator.generate(
            prompt=prompt,
            system_instruction=get_system_instruction(lang),
            temperature=request.temperature,
        )

        return ChatCompareResponse(
            session_id=request.session_id,
            comparison=comparison,
            language=lang,
            products=found_sources,
        )