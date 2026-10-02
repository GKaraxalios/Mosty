"""Chat service: orchestrates intent extraction + smart search + styling generation.

This is the high-level service that powers the /chat/* endpoints.
Implements the ELIZA AI AGENT Stylist architecture with fashion-aware intent
extraction, two-phase search (reference product → target category),
and stylist-quality response generation. Supports weather-aware outfit
recommendations when location is provided.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import (
    ActiveSearchState,
    ChatCompareRequest,
    ChatCompareResponse,
    ChatRecommendRequest,
    ChatRecommendResponse,
    ChatRequest,
    ChatResponse,
    HybridSearchFilters,
    ProductCard,
    ProductReasonSignal,
    ProductReasoning,
    RAGSearchResult,
    RAGSourceCitation,
)
from app.services.cache_service import CacheService
from app.services.hybrid_search_service import HybridSearchService
from app.services.memory_service import MemoryService, get_memory_service
from app.services.rag_ingestion_service import get_chunk_store
from app.services.rerank_service import maybe_rerank
from app.services.conversation_shopping_postfilter import exclude_already_shown_skus
from app.services.soft_style_rerank import rerank_chat_retrieval_results
from app.services.vertex_service import VertexGenerativeService
from app.utils.commerce_semantics import enrich_search_query, prune_by_excluded_keywords, fold_el
from app.utils.brand_resolution import resolve_designer_from_text, normalize_brand_name
from app.utils.conversation_refinement import (
    is_repeated_user_message,
    wants_exclude_already_shown_skus,
)
from app.utils.conversation_state import (
    apply_active_state_to_hybrid_filters,
    filter_results_by_candidate_skus,
    merge_active_search_state,
)
from app.utils.entity_fallback import apply_entity_fallback
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
from app.utils.shopping_prompt_blocks import build_shopping_assistant_block
from app.utils.chat_response_sanitize import (
    fallback_no_products,
    fallback_product_intro,
    sanitize_visible_assistant_text,
)
from app.utils.store_urls import absolutize_media_url, get_storefront_origin
from app.utils.transformers import rag_document_to_text
from app.services.weather_service import get_weather_by_city, get_weather_by_coords

logger = get_logger(__name__)

# Cities we try to extract from the user message when client doesn't send location (longest first).
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


def _extract_city_from_message(text: str) -> str | None:
    """Extract a known city name from the user message for weather lookup.

    Used when the client does not send location_city/lat/lon so we still fetch
    weather when the user says e.g. 'στην Αθήνα' or 'τι να φορέσω σήμερα Αθήνα'.
    """
    if not text or not text.strip():
        return None
    normalized = " " + text.strip().lower() + " "
    for city in _CITIES_IN_MESSAGE:
        if not city:
            continue
        # Case-insensitive: city as whole word (with space or start/end)
        needle = " " + city.lower() + " "
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


def _apply_intent_to_session_prefs(
    memory: MemoryService,
    session_id: str,
    intent: FashionIntent,
) -> None:
    """Persist facet hints for the next turn (category/colors/occasion/fit/styles)."""
    sess = memory.get_session(session_id)
    if not sess:
        return
    prefs = sess.user_preferences
    kwargs: dict[str, Any] = {}
    if intent.target_category:
        cats = list(prefs.preferred_categories)
        if intent.target_category not in cats:
            cats.append(intent.target_category)
        kwargs["preferred_categories"] = cats
    if intent.colors:
        kwargs["preferred_colors"] = list(intent.colors)
    if intent.occasion:
        kwargs["occasion"] = intent.occasion
    if intent.fit:
        kwargs["fit_preference"] = intent.fit
    if intent.style_keywords:
        styles = list(prefs.preferred_styles)
        for x in intent.style_keywords:
            if x not in styles:
                styles.append(x)
        kwargs["preferred_styles"] = styles
    if kwargs:
        memory.update_preferences(session_id, **kwargs)


def _merge_intent_into_hybrid_filters(
    filters: HybridSearchFilters,
    intent: FashionIntent,
    prefs: Any,
) -> None:
    """Fill HybridSearchFilters from extracted intent + sticky session preferences."""
    if not filters.category:
        if intent.target_category:
            filters.category = intent.target_category
        elif getattr(prefs, "preferred_categories", None):
            filters.category = prefs.preferred_categories[-1]
    if not filters.color:
        if intent.colors:
            filters.color = intent.colors[0]
        elif getattr(prefs, "preferred_colors", None) and prefs.preferred_colors:
            filters.color = prefs.preferred_colors[0]
    if not filters.material and intent.materials:
        filters.material = intent.materials[0]
    if not filters.occasion and intent.occasion:
        filters.occasion = intent.occasion
    if not filters.season and intent.season:
        filters.season = intent.season
    if not filters.fit and intent.fit:
        filters.fit = intent.fit
    if not filters.size and intent.size:
        filters.size = intent.size


def _dedupe_by_sku(results: list[RAGSearchResult]) -> list[RAGSearchResult]:
    """Keep only the highest-scoring chunk per unique SKU."""
    best: dict[str, RAGSearchResult] = {}
    for r in results:
        if r.sku not in best or r.score > best[r.sku].score:
            best[r.sku] = r
    return sorted(best.values(), key=lambda x: x.score, reverse=True)


# Fallback styling text when AI omits or returns empty styling_reason (Greek, premium tone).
STYLING_FALLBACKS_EL = [
    "Μια κομψή επιλογή που συνδυάζεται εύκολα με διαφορετικά looks.",
    "Ιδανική πρόταση για ένα προσεγμένο και σύγχρονο outfit.",
    "Ένα ευέλικτο κομμάτι που μπορεί να φορεθεί εύκολα σε πολλές εμφανίσεις.",
    "Καθαρές γραμμές και πολυτέλεια για καθημερινό στυλ.",
    "Ταιριάζει ιδανικά σε casual και πιο dressy περιστάσεις.",
]


def _normalize_styling_reason(val: str | None) -> str:
    """Treat null, empty, whitespace, and literal 'null'/'undefined' as missing."""
    if val is None:
        return ""
    s = (val if isinstance(val, str) else str(val)).strip()
    if not s or s.lower() in ("null", "undefined"):
        return ""
    return s


def _styling_fallback(sku: str) -> str:
    """Pick a consistent fallback by SKU so the same product gets the same fallback."""
    idx = hash(sku) % len(STYLING_FALLBACKS_EL)
    return STYLING_FALLBACKS_EL[idx]


def _build_product_cards(
    results: list[RAGSearchResult],
    styling_map: dict[str, str] | None = None,
    popularity_map: dict[str, str] | None = None,
    badge_map: dict[str, str] | None = None,
    use_popularity: bool = False,
    intent: FashionIntent | None = None,
) -> list[ProductCard]:
    """Build structured product cards. When use_popularity=True, prefer popularity_reason over styling_reason."""
    cards: list[ProductCard] = []
    seen: set[str] = set()
    storefront_origin = get_storefront_origin()
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
                popularity_reason = "Επιλογή που ξεχωρίζει στο MOSTY" if not badge else f"{badge} στο MOSTY"
            if not badge:
                badge = "Featured"
            final_styling = ""
        else:
            normalized = _normalize_styling_reason(raw_styling)
            final_styling = normalized or _styling_fallback(r.sku)
            popularity_reason = ""
            badge = ""

        logger.debug(
            "product_card sku=%r styling_len=%d popularity=%s badge=%s",
            r.sku, len(final_styling), bool(popularity_reason), badge or "(none)",
        )
        cats = meta.get("categories") or []
        cats_l = [str(c).lower() for c in cats] if isinstance(cats, list) else []
        product_type = str(meta.get("product_type") or "").lower()

        is_accessory = any(
            ac in cats_l for ac in ["accessories", "scarves", "hats", "jewellery", "belts"]
        )
        is_occasion_specific = any(
            k in product_type for k in ["evening", "party", "ceremony", "wedding"]
        )
        is_outfit_anchor = any(
            core in product_type for core in ["jean", "trouser", "pant", "skirt", "dress"]
        )
        main_category = None
        if isinstance(cats, list) and cats:
            main_category = str(cats[0])

        reasoning = _build_product_reasoning(
            meta=meta,
            intent=intent,
            main_category=main_category,
            price_val=price_val,
        )

        cards.append(
            ProductCard(
                sku=r.sku,
                name=r.name,
                image=absolutize_media_url(meta.get("image_url"), storefront_origin),
                price=price_str,
                currency="EUR",
                availability=availability,
                url=r.url,
                styling_reason=final_styling,
                score=r.score,
                category=main_category,
                is_accessory=is_accessory,
                is_occasion_specific=is_occasion_specific,
                is_outfit_anchor=is_outfit_anchor,
                popularity_reason=popularity_reason,
                badge=badge,
                reasoning=reasoning,
            )
        )
    return cards


def _build_product_reasoning(
    *,
    meta: dict,
    intent: FashionIntent | None,
    main_category: str | None,
    price_val: float | None,
) -> ProductReasoning:
    """Translate ranking/filtering signals into user-facing reasoning."""
    signals_raw = meta.get("reason_signals") or []
    if not isinstance(signals_raw, list):
        signals_raw = []
    signals: list[ProductReasonSignal] = []
    for s in signals_raw:
        try:
            signals.append(
                ProductReasonSignal(
                    type=str(s.get("type")),
                    strength=str(s.get("strength", "medium")),
                    value=(str(s.get("value")) if s.get("value") is not None else None),
                )
            )
        except Exception:
            continue

    reasoning = ProductReasoning(signals=signals, fallback=False, short_reason="")
    brand = (meta.get("brand") or "") or None

    # Brand-driven reasoning
    if intent and intent.brand:
        has_exact_brand = any(sig.type == "brand_exact_match" for sig in signals)
        has_fallback_style = any(sig.type == "fallback_similar_style" for sig in signals)
        if has_exact_brand:
            if brand:
                reasoning.short_reason = f"Προτάθηκε επειδή είναι προϊόν {brand} και ταιριάζει με το στυλ που ζήτησες."
            else:
                reasoning.short_reason = "Προτάθηκε επειδή ανήκει στο brand που ζήτησες και ταιριάζει με το στυλ που περιέγραψες."
        elif has_fallback_style:
            reasoning.short_reason = (
                "Προτάθηκε ως κομμάτι με παρόμοια αισθητική, επειδή δεν βρέθηκαν αρκετά ακριβή αποτελέσματα του ίδιου brand."
            )
            reasoning.fallback = True

    # Occasion-based reasoning
    if not reasoning.short_reason and intent and intent.occasion:
        reasoning.short_reason = f"Προτάθηκε επειδή είναι κατάλληλο για {intent.occasion}, όπως ζήτησες."

    # Category/style reasoning
    if not reasoning.short_reason and main_category:
        reasoning.short_reason = (
            f"Προτάθηκε επειδή ανήκει στην κατηγορία {main_category} και ταιριάζει με το στυλ που περιέγραψες."
        )

    # Budget hint
    budget_max = getattr(intent, "budget_max", None) if intent else None
    if budget_max is not None and price_val is not None and price_val <= budget_max:
        signals.append(
            ProductReasonSignal(
                type="price_match",
                strength="high",
                value=f"{price_val} <= {budget_max}",
            )
        )
        if reasoning.short_reason:
            reasoning.short_reason += " Βρίσκεται επίσης εντός του budget σου."

    if not reasoning.short_reason:
        reasoning.short_reason = "Προτάθηκε επειδή ταιριάζει με τα κριτήρια που έδωσες."

    reasoning.signals = signals
    return reasoning


_JSON_RE = re.compile(r"\{[\s\S]*\}", re.DOTALL)


def _parse_stylist_json(raw: str) -> tuple[str, dict[str, str], bool, dict[str, str], dict[str, str]]:
    """Parse the LLM's structured JSON response.

    Returns (message, styling_map, llm_returned_no_products, popularity_map, badge_map).
    - popularity_map: sku -> popularity_reason (for popular_products intent).
    - badge_map: sku -> badge ("Best seller", "Featured", etc.).
    Falls back to (raw_text, {}, False, {}, {}) if JSON parsing fails.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)

    m = _JSON_RE.search(text)
    if not m:
        return "", {}, False, {}, {}

    try:
        data = json.loads(m.group())
    except json.JSONDecodeError:
        return "", {}, False, {}, {}

    message = data.get("message", "")
    products = data.get("products", [])
    no_results = data.get("no_results", False)
    llm_returned_no_products = no_results or (isinstance(products, list) and len(products) == 0)

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


class ChatService:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._search = HybridSearchService()
        self._generator = VertexGenerativeService()
        self._memory = get_memory_service()
        self._cache = CacheService()

    # ── Intent extraction ────────────────────────────────────────────────

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
                max_output_tokens=300,
            )
            return parse_intent(raw, query)
        except Exception:
            logger.warning("Intent extraction failed, using fallback")
            return FashionIntent(search_query=query)

    # ── Reference product lookup ─────────────────────────────────────────

    async def _find_reference_product(
        self, name: str
    ) -> RAGSearchResult | None:
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

    # ── Query rewrite (used by recommend / search endpoints) ─────────────

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

    async def _search_products_with_optional_relaxation(
        self,
        effective_query: str,
        filters: HybridSearchFilters,
        request: ChatRequest,
        intent: FashionIntent,
        merged_state: ActiveSearchState,
        user_query: str,
        session_shown_skus: list[str],
        has_conversation_history: bool,
        repeated_user_message: bool,
        rejected_skus: list[str] | None = None,
    ) -> tuple[list[RAGSearchResult], HybridSearchFilters, bool, bool]:
        """Run hybrid search + validation; if nothing usable, relax filters and retry.

        Typical failure: metadata pre-filter removes all chunks (color not in product text)
        or post-filters strip every hit. Progressive relaxation restores BM25/dense matches.

        Returns (results, filters_used, postfilter_relaxed_duplicates, exclude_shown_applied).
        """
        filters_used = filters.model_copy()

        exclude_active = wants_exclude_already_shown_skus(
            user_query=user_query,
            has_conversation_history=has_conversation_history,
            restrict_to_candidates=merged_state.restrict_to_candidates,
            repeated_user_message=repeated_user_message,
            intent_novelty_request=intent.novelty_request,
            intent_refinement_request=intent.refinement_request,
        )
        exclude_applied = bool(exclude_active and session_shown_skus)
        shown_frozen = frozenset(session_shown_skus)
        rejected_frozen = frozenset(rejected_skus or [])
        last_relaxed = False

        async def run_once(f: HybridSearchFilters) -> list[RAGSearchResult]:
            nonlocal last_relaxed
            raw = await self._search.search(
                query=effective_query,
                top_k=self._settings.RERANK_TOP_N,
                filters=f,
            )
            r = maybe_rerank(effective_query, raw, top_k=request.top_k)
            # Derive coarse-grained use_case / desired_output from the raw query
            uq = (user_query or "").lower()
            use_case = None
            desired_output = None
            if any(x in uq for x in ["καθημεριν", "daily", "everyday", "συχν", "κάθε μέρα"]):
                use_case = "daily_wear"
            if any(x in uq for x in ["outfit", "σύνολ", "συνολ", "look", "σετ"]):
                desired_output = "outfit"

            r = rerank_chat_retrieval_results(
                r,
                effective_query=effective_query,
                merged_state=merged_state,
                user_query=user_query,
                intent_novelty_request=intent.novelty_request,
                intent_refinement_request=intent.refinement_request,
                use_case=use_case,
                desired_output=desired_output,
                brand=intent.brand,
                brand_mode=intent.brand_mode,
            )
            r = _dedupe_by_sku(r)
            r = filter_valid_results(r)
            r = prune_by_excluded_keywords(r, intent.excluded_keywords)
            if rejected_frozen:
                r = [x for x in r if x.sku not in rejected_frozen]
            r, relaxed = exclude_already_shown_skus(
                r,
                shown_frozen,
                active=exclude_active,
            )
            last_relaxed = relaxed
            return r

        results = await run_once(filters_used)
        if results:
            return results, filters_used, last_relaxed, exclude_applied

        if not self._settings.HYBRID_RELAX_ON_EMPTY:
            return results, filters_used, last_relaxed, exclude_applied

        if filters_used.color:
            filters_used = filters_used.model_copy(update={"color": None})
            logger.info("chat_ask: retrying search without color filter (no prior results)")
            results = await run_once(filters_used)
            if results:
                return results, filters_used, last_relaxed, exclude_applied

        if any(
            [
                filters_used.material,
                filters_used.occasion,
                filters_used.fit,
                filters_used.style,
                filters_used.season,
            ]
        ):
            filters_used = filters_used.model_copy(
                update={
                    "material": None,
                    "occasion": None,
                    "fit": None,
                    "style": None,
                    "season": None,
                }
            )
            logger.info("chat_ask: retrying without material/occasion/fit/style/season filters")
            results = await run_once(filters_used)
            if results:
                return results, filters_used, last_relaxed, exclude_applied

        if filters_used.category:
            filters_used = filters_used.model_copy(update={"category": None})
            logger.info("chat_ask: retrying search without category filter")
            results = await run_once(filters_used)
            if results:
                return results, filters_used, last_relaxed, exclude_applied

        if filters_used.size:
            filters_used = filters_used.model_copy(update={"size": None})
            logger.info("chat_ask: retrying search without size filter")
            results = await run_once(filters_used)
            if results:
                return results, filters_used, last_relaxed, exclude_applied

        return results, filters_used, last_relaxed, exclude_applied

    # ── Main ask endpoint ────────────────────────────────────────────────

    async def ask(self, request: ChatRequest) -> ChatResponse:
        lang = resolve_language(request.language, request.query)
        session = self._memory.get_or_create(request.session_id, language=lang)

        # Conversation context (before adding current turn)
        context = self._memory.get_conversation_context(session.session_id)
        # request.filters = client/UI sticky filters only (often empty). Structured
        # attributes from natural language are merged later from FashionIntent (see chat_ask retrieval log).
        logger.info(
            "chat_ask context session=%s lang=%s query_len=%s top_k=%s request_filters=%s city=%s",
            session.session_id,
            lang,
            len(request.query or ""),
            request.top_k,
            request.filters,
            request.location_city,
        )
        logger.info("chat_ask context_preview: %s", context[:2000] if context else "")

        has_conversation_history = bool((context or "").strip())
        repeated_user_message = is_repeated_user_message(
            request.query, session.last_query
        )

        # Step 1: Extract fashion intent
        intent = await self._extract_intent(request.query, context, lang)

        # ── Brand/designer canonicalization and intent stabilization ────────────
        # 1) Canonicalize brand if LLM set one (e.g. Greek spelling of Latin brand).
        if intent.brand:
            did, canonical = normalize_brand_name(intent.brand)
            if did:
                intent.brand = canonical or intent.brand

        # 2) If LLM didn't set brand, try to resolve designer from raw query text.
        if not intent.brand:
            designer_id, canonical = resolve_designer_from_text(request.query)
            if designer_id:
                intent.brand = canonical or designer_id.replace("_", " ").title()
                q_fold = fold_el(request.query)
                # Heuristic: phrases that imply "style like this brand"
                if any(p in q_fold for p in ["στυλ σε", "σε στυλ", "σε υφος", "σε ύφος", "σε φαση", "σε φάση"]):
                    intent.brand_mode = intent.brand_mode or "style_like"
                else:
                    intent.brand_mode = intent.brand_mode or "exact"

        # 3) Reuse brand from session preferences on follow-ups if user hints "same style"
        if not intent.brand and session.user_preferences.preferred_brands:
            q_fold = fold_el(request.query)
            if any(p in q_fold for p in ["αλλα", "άλλα", "ιδιο", "ίδιο", "σε στυλ", "σε υφος", "σε ύφος"]):
                last_brand = session.user_preferences.preferred_brands[-1]
                intent.brand = last_brand
                intent.brand_mode = intent.brand_mode or "exact"

        # Gate: support/store intents → info-only answer, no product retrieval
        if not should_retrieve_products(intent.intent_type):
            logger.info(
                "Info-only intent type=%s, skipping product retrieval",
                intent.intent_type,
            )
            self._memory.add_turn(session.session_id, "user", request.query)
            prompt = build_info_only_prompt(request.query, lang)
            raw_info = await self._generator.generate(
                prompt=prompt,
                system_instruction=get_system_instruction(lang),
                temperature=request.temperature,
            )
            answer = sanitize_visible_assistant_text(raw_info, lang=lang)
            if not answer:
                answer = (
                    "Μπορώ να σε βοηθήσω με πληροφορίες για το MOSTY (ώρες, αποστολές, επιστροφές). Τι θέλεις να μάθεις;"
                    if lang == "el"
                    else "I can help with MOSTY store info (hours, shipping, returns). What would you like to know?"
                )
            self._memory.add_turn(session.session_id, "assistant", answer)
            return ChatResponse(
                session_id=session.session_id,
                query=request.query,
                rewritten_query=None,
                answer=answer,
                language=lang,
                sources=[],
                products=[],
            )

        # Deterministic facets when LLM intent omits colors/category/materials (production safeguard)
        intent = apply_entity_fallback(request.query, intent)

        # Multi-turn merged state (price ranges, candidate-set phrases, exclusions)
        prev_state = session.user_preferences.active_search_state
        last_shown = list(prev_state.last_result_skus) if prev_state else []
        merged_state = merge_active_search_state(
            prev_state,
            intent,
            request.query,
            bool((context or "").strip()),
            last_shown,
        )
        intent.excluded_keywords = list(merged_state.excluded_keywords)

        # Step 2: Find reference product if the user mentioned one by name
        ref_product: RAGSearchResult | None = None
        if intent.reference_product:
            ref_product = await self._find_reference_product(
                intent.reference_product
            )

        # Step 2b: Sticky prefs for follow-up turns (same session object is updated in-repo)
        _apply_intent_to_session_prefs(self._memory, session.session_id, intent)
        if intent.brand:
            # Accumulate preferred brands for this session so we can reuse later.
            brands = list(session.user_preferences.preferred_brands or [])
            if intent.brand not in brands:
                brands.append(intent.brand)
                self._memory.update_preferences(session.session_id, preferred_brands=brands)

        # Step 3: Build search filters (API body + budget + intent + session memory)
        filters = request.filters.model_copy()
        prefs = session.user_preferences
        if prefs.budget_min and not filters.min_price:
            filters.min_price = prefs.budget_min
        if prefs.budget_max and not filters.max_price:
            filters.max_price = prefs.budget_max
        _merge_intent_into_hybrid_filters(filters, intent, prefs)
        apply_active_state_to_hybrid_filters(filters, merged_state)

        # ── Brand-aware filters: two-stage retrieval ────────────────────────────
        # Primary pass: always try strict brand match when brand is resolved.
        primary_filters = filters.model_copy()
        fallback_filters = filters.model_copy()
        if intent.brand:
            primary_filters.brand = intent.brand
        # Fallback (style-like): relax brand filter so similar-style products can appear.
        fallback_filters.brand = None

        effective_query = enrich_search_query(
            intent.search_query,
            {
                "colors": intent.colors,
                "materials": intent.materials,
                "occasion": intent.occasion,
                "season": intent.season,
                "fit": intent.fit,
                "target_category": intent.target_category,
                "style_keywords": merged_state.style_keywords or intent.style_keywords,
            },
        )

        logger.info(
            "chat_ask retrieval session=%s intent_type=%s target_category=%r "
            "intent_colors=%s intent_search_query=%r merged_filters=%s effective_query=%r "
            "active_state=%s",
            session.session_id,
            intent.intent_type,
            intent.target_category,
            intent.colors,
            intent.search_query,
            filters.model_dump(exclude_none=True),
            effective_query[:800] if effective_query else "",
            merged_state.model_dump(exclude_none=True),
        )

        # Step 4: Intent-driven search (brand-first + relaxation when needed)
        # 4a: strict brand match (when brand resolved) or generic filters otherwise
        (
            results_primary,
            filters_used_primary,
            postfilter_relaxed,
            exclude_shown_applied,
        ) = await self._search_products_with_optional_relaxation(
            effective_query,
            primary_filters,
            request,
            intent,
            merged_state,
            request.query,
            list(session.retrieved_skus or []),
            has_conversation_history,
            repeated_user_message,
            rejected_skus=list(prefs.rejected_skus or []),
        )
        results_primary = filter_results_by_candidate_skus(results_primary, merged_state)

        results = results_primary
        filters = filters_used_primary
        used_brand_fallback = False

        # 4b: if no strict-brand results and brand intent is style_like, relax brand filter
        if intent.brand and intent.brand_mode == "style_like" and not results_primary:
            (
                results_fallback,
                filters_used_fallback,
                postfilter_relaxed,
                exclude_shown_applied,
            ) = await self._search_products_with_optional_relaxation(
                effective_query,
                fallback_filters,
                request,
                intent,
                merged_state,
                request.query,
                list(session.retrieved_skus or []),
                has_conversation_history,
                repeated_user_message,
                rejected_skus=list(prefs.rejected_skus or []),
            )
            results_fallback = filter_results_by_candidate_skus(results_fallback, merged_state)
            results = results_fallback
            filters = filters_used_fallback
            used_brand_fallback = True

        rewritten = (
            effective_query
            if effective_query != request.query
            else None
        )

        if not results:
            self._memory.update_preferences(
                session.session_id,
                active_search_state=merged_state,
            )
            self._memory.add_turn(session.session_id, "user", request.query)
            answer = build_no_results_response(lang)
            self._memory.add_turn(session.session_id, "assistant", answer)
            return ChatResponse(
                session_id=session.session_id,
                query=request.query,
                rewritten_query=rewritten,
                answer=answer,
                language=lang,
                sources=[],
                products=[],
            )

        # Step 4b: Optional weather for outfit recommendations (Open-Meteo, no API key)
        # Use location from request, or extract city from message when client didn't send it
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
        except Exception as e:
            logger.warning("Weather fetch failed: %s", e)

        # Step 5: Build stylist prompt (filters valid results inside; uses JSON output mode)
        shopping_block = build_shopping_assistant_block(
            lang,
            exclude_shown_applied=exclude_shown_applied,
            relaxed_duplicate_skus=postfilter_relaxed,
            repeated_user_message=repeated_user_message,
        )
        prompt = build_stylist_prompt(
            query=request.query,
            intent=intent,
            results=results,
            lang=lang,
            conversation_context=context,
            ref_product=ref_product,
            weather_context=weather_context,
            shopping_assistant_block=shopping_block,
        )
        system_instruction = get_system_instruction(lang, output_mode="json")
        if getattr(self._settings, "LOG_STYLIST_PROMPT", False):
            logger.info(
                "========== FULL STYLIST PROMPT (LOG_STYLIST_PROMPT=true) ==========\n"
                "--- SYSTEM ---\n%s\n--- USER ---\n%s\n========== END PROMPT ==========",
                system_instruction,
                prompt,
            )

        self._memory.add_turn(session.session_id, "user", request.query)

        # Step 6: Generate structured stylist answer (higher token limit to reduce truncation)
        raw_answer = await self._generator.generate(
            prompt=prompt,
            system_instruction=system_instruction,
            temperature=request.temperature,
            max_output_tokens=4096,
        )

        # Step 7: Parse structured JSON and build product cards
        message, styling_map, llm_returned_no_products, popularity_map, badge_map = _parse_stylist_json(raw_answer)
        is_popular_intent = (intent.intent_type or "").lower() == "popular_products"

        # Only treat llm_returned_no_products as authoritative when retrieval actually
        # returned no usable results. If we already have results, ignore this flag to
        # avoid false «no products» messaging.
        if llm_returned_no_products and message and not results:
            merged_state.last_result_skus = [r.sku for r in results]
            self._memory.update_preferences(
                session.session_id,
                active_search_state=merged_state,
            )
            visible = sanitize_visible_assistant_text(message, lang=lang) or fallback_no_products(lang)
            self._memory.add_turn(session.session_id, "assistant", visible)
            return ChatResponse(
                session_id=session.session_id,
                query=request.query,
                rewritten_query=rewritten,
                answer=visible,
                language=lang,
                sources=[],
                products=[],
                intent=intent.intent_type,
            )

        if styling_map or popularity_map:
            ordered_results = []
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
                intent=intent,
            )
        else:
            product_cards = _build_product_cards(
                results,
                popularity_map=popularity_map or None,
                badge_map=badge_map or None,
                use_popularity=is_popular_intent,
                intent=intent,
            )

        visible = sanitize_visible_assistant_text((message or "").strip(), lang=lang)
        if not visible:
            visible = (
                fallback_product_intro(lang)
                if product_cards
                else fallback_no_products(lang)
            )
        # Safety guard: when we do have product cards, never allow the visible text to
        # claim that the brand or products are unavailable. The LLM may hallucinate
        # "δεν διαθέτουμε προϊόντα" even when retrieval succeeded; override such text.
        if product_cards:
            low = visible.lower()
            negative_markers = [
                "δεν διαθέτουμε προϊόντα",
                "δεν διαθέτουμε",
                "δεν βρήκα κάτι",
                "δεν βρηκα κατι",
                "δεν υπάρχουν προϊόντα",
                "δεν υπαρχουν προιοντα",
            ]
            if any(m in low for m in negative_markers):
                visible = fallback_product_intro(lang)
        answer = visible

        self._memory.add_turn(session.session_id, "assistant", answer)
        self._memory.add_retrieved_skus(
            session.session_id, [r.sku for r in results]
        )
        merged_state.last_result_skus = [r.sku for r in results]
        merged_state.restrict_to_candidates = False
        merged_state.candidate_skus = []
        self._memory.update_preferences(
            session.session_id,
            active_search_state=merged_state,
        )
        self._memory.update_session(
            session.session_id,
            last_query=request.query,
            last_answer=answer,
            language=lang,
        )

        response_metadata = None
        if is_popular_intent:
            response_metadata = {"ranking_source": "curated"}
        if intent.target_category:
            response_metadata = response_metadata or {}
            response_metadata["target_category"] = intent.target_category

        # For now we only expose structured location/map for explicit location intents.
        location_payload = None
        map_payload = None
        if intent.intent_type in ("location_based_query", "store_lookup") and intent.location:
            try:
                # intent.location should be a dict-like payload (backend-controlled)
                location_payload = intent.location
                map_payload = {"location": location_payload, "provider": "google", "zoom": 14}
            except Exception:
                location_payload = None
                map_payload = None

        return ChatResponse(
            session_id=session.session_id,
            query=request.query,
            rewritten_query=rewritten,
            answer=answer,
            message=answer,
            language=lang,
            sources=_dedupe_sources(results),
            products=product_cards,
            intent=intent.intent_type,
            location=location_payload,
            map=map_payload,
            metadata=response_metadata,
        )

    async def search(self, request: ChatRequest) -> list[RAGSearchResult]:
        lang = resolve_language(request.language, request.query)
        session = self._memory.get_or_create(request.session_id, language=lang)

        search_query, _ = await self._maybe_rewrite_query(
            request.query, session.session_id, lang
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
            request.query, session.session_id, lang
        )

        raw_results = await self._search.search(
            query=search_query,
            top_k=self._settings.RERANK_TOP_N,
            filters=request.filters,
        )
        results = maybe_rerank(search_query, raw_results, top_k=request.top_k)

        if not results:
            self._memory.add_turn(session.session_id, "user", request.query)
            answer = build_no_results_response(lang)
            self._memory.add_turn(session.session_id, "assistant", answer)
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

        raw_rec = await self._generator.generate(
            prompt=prompt,
            system_instruction=get_system_instruction(lang),
            temperature=request.temperature,
        )
        answer = sanitize_visible_assistant_text(raw_rec, lang=lang)
        if not answer:
            answer = (
                "Έχω επιλέξει μερικές προτάσεις με βάση την αναζήτησή σου — δες τις παρακάτω."
                if lang == "el"
                else "Here are some suggestions based on your search — see below."
            )

        self._memory.add_turn(session.session_id, "assistant", answer)
        self._memory.add_retrieved_skus(
            session.session_id, [r.sku for r in results]
        )

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
                        sku=sku, name=chunks[0].name,
                        url=chunks[0].url, score=1.0,
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
