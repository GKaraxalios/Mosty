from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Shared / generic ────────────────────────────────────────────────────────


class APIResponse(BaseModel):
    success: bool = True


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    detail: str | None = None


# ── Price ────────────────────────────────────────────────────────────────────


class PriceSchema(BaseModel):
    currency: str = "EUR"
    regular: float | None = None
    sale: float | None = None
    current: float | None = None
    discount_percent: float | None = None


# ── Category ─────────────────────────────────────────────────────────────────


class CategorySchema(BaseModel):
    name: str
    slug: str | None = None


# ── Image ────────────────────────────────────────────────────────────────────


class ImageSchema(BaseModel):
    url: str
    label: str | None = None


# ── Content ──────────────────────────────────────────────────────────────────


class ContentSchema(BaseModel):
    short: str | None = None
    long: str | None = None
    text: str | None = None


# ── Product (REST response) ─────────────────────────────────────────────────


class ProductSchema(BaseModel):
    sku: str
    name: str
    type: str | None = None
    url: str | None = None
    price: PriceSchema = Field(default_factory=PriceSchema)
    categories: list[CategorySchema] = Field(default_factory=list)
    images: list[ImageSchema] = Field(default_factory=list)
    content: ContentSchema = Field(default_factory=ContentSchema)
    meta_keyword: str | None = None
    meta_description: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ProductListResponse(APIResponse):
    page: int = 1
    per_page: int = 50
    total: int = 0
    total_pages: int = 0
    items: list[ProductSchema] = Field(default_factory=list)


class ProductDetailResponse(APIResponse):
    product: ProductSchema


class ProductLiveResponse(APIResponse):
    sku: str
    price: PriceSchema = Field(default_factory=PriceSchema)
    url: str | None = None
    updated_at: str | None = None
    in_stock: bool | None = None


# ── RAG Document ─────────────────────────────────────────────────────────────


class RAGDocument(BaseModel):
    sku: str
    name: str
    type: str | None = None
    canonical_url: str | None = None
    all_urls: list[str] = Field(default_factory=list)
    description_text: str | None = None
    description_html: str | None = None
    meta_keyword: str | None = None
    meta_description: str | None = None
    categories: list[str] = Field(default_factory=list)
    image_url: str | None = None
    currency: str = "EUR"
    regular_price: float | None = None
    final_price: float | None = None
    discount_percent: float | None = None
    created_at: str | None = None
    updated_at: str | None = None
    stock_status: str | None = None


class RAGChunk(BaseModel):
    chunk_id: str
    sku: str
    name: str
    url: str | None = None
    categories: list[str] = Field(default_factory=list)
    price: float | None = None
    updated_at: str | None = None
    source: str = "magento_graphql"
    text: str = ""
    meta_keyword: str | None = None
    meta_description: str | None = None
    description_text: str | None = None
    stock_status: str | None = None
    image_url: str | None = None


# ── RAG Sync ─────────────────────────────────────────────────────────────────


class RAGSyncResponse(APIResponse):
    products_synced: int = 0
    chunks_created: int = 0
    embeddings_generated: int = 0
    index_updated: bool = False


class RAGSyncSingleResponse(APIResponse):
    sku: str
    chunks_created: int = 0
    embeddings_generated: int = 0
    index_updated: bool = False


class RAGDeleteResponse(APIResponse):
    sku: str
    chunks_removed: int = 0


class RAGIndexStatusResponse(BaseModel):
    """Index status from GCS/shared state — use to check if full sync has finished."""
    chunk_count: int = 0
    indexed_sku_count: int = 0
    last_successful_sync_at: str | None = None
    last_run_at: str | None = None
    last_error: str | None = None


# ── RAG Search / Ask ─────────────────────────────────────────────────────────


class RAGSearchRequest(BaseModel):
    query: str
    top_k: int = 5


class RAGSearchResult(BaseModel):
    score: float
    dense_score: float | None = None
    sparse_score: float | None = None
    rerank_score: float | None = None
    sku: str
    name: str
    chunk_text: str
    url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RAGSearchResponse(APIResponse):
    query: str
    results: list[RAGSearchResult] = Field(default_factory=list)


class RAGAskRequest(BaseModel):
    query: str
    top_k: int = 5
    temperature: float = 0.2


class RAGSourceCitation(BaseModel):
    sku: str
    name: str
    url: str | None = None
    score: float


class RAGAskResponse(APIResponse):
    query: str
    answer: str
    language: str = "el"
    sources: list[RAGSourceCitation] = Field(default_factory=list)


# ── Hybrid Search ────────────────────────────────────────────────────────────


class HybridSearchFilters(BaseModel):
    category: str | None = None
    brand: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    language: str | None = None
    product_type: str | None = None
    color: str | None = None
    size: str | None = None
    occasion: str | None = None
    style: str | None = None
    material: str | None = None
    season: str | None = None
    fit: str | None = None
    sale_only: bool | None = None
    new_arrivals_only: bool | None = None


class HybridSearchRequest(BaseModel):
    query: str
    top_k: int = 10
    filters: HybridSearchFilters = Field(default_factory=HybridSearchFilters)


class HybridSearchResponse(APIResponse):
    query: str
    results: list[RAGSearchResult] = Field(default_factory=list)
    dense_weight: float = 0.7
    sparse_weight: float = 0.3


# ── Background Jobs ─────────────────────────────────────────────────────────


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(str, Enum):
    FULL_SYNC = "full_sync"
    SINGLE_SYNC = "single_sync"
    INCREMENTAL_SYNC = "incremental_sync"


class JobSchema(BaseModel):
    job_id: str
    type: JobType
    status: JobStatus = JobStatus.PENDING
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    total_products: int = 0
    processed_products: int = 0
    total_chunks: int = 0
    processed_chunks: int = 0
    errors: list[str] = Field(default_factory=list)
    summary: str | None = None
    sku: str | None = None


class JobCreateResponse(APIResponse):
    job: JobSchema


class JobListResponse(APIResponse):
    jobs: list[JobSchema] = Field(default_factory=list)


# ── Scheduler ────────────────────────────────────────────────────────────────


class SchedulerStatusResponse(APIResponse):
    enabled: bool = False
    running: bool = False
    full_sync_cron: str = ""
    incremental_sync_interval_minutes: int = 0
    incremental_sync_enabled: bool = False
    next_full_sync: str | None = None
    next_incremental_sync: str | None = None


# ── Chat / Conversational Memory ────────────────────────────────────────────


class ConversationTurn(BaseModel):
    role: str  # "user" | "assistant"
    content: str
    timestamp: str = ""


class ActiveSearchState(BaseModel):
    """Persisted multi-turn search state (merged with each product intent)."""

    category: str | None = None
    color: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    material: str | None = None
    occasion: str | None = None
    fit: str | None = None
    size: str | None = None
    season: str | None = None
    style: str | None = None
    # Accumulated soft style lemmas across turns (rerank / query enrich); style is primary facet string.
    style_keywords: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    candidate_skus: list[str] = Field(default_factory=list)
    last_result_skus: list[str] = Field(default_factory=list)
    restrict_to_candidates: bool = False


class UserPreferences(BaseModel):
    budget_min: float | None = None
    budget_max: float | None = None
    preferred_colors: list[str] = Field(default_factory=list)
    preferred_categories: list[str] = Field(default_factory=list)
    preferred_styles: list[str] = Field(default_factory=list)
    occasion: str | None = None
    language: str = "auto"
    rejected_skus: list[str] = Field(default_factory=list)
    liked_skus: list[str] = Field(default_factory=list)
    preferred_sizes: list[str] = Field(default_factory=list)
    preferred_brands: list[str] = Field(default_factory=list)
    fit_preference: str | None = None
    active_search_state: ActiveSearchState | None = None


class ChatSession(BaseModel):
    session_id: str
    created_at: str = ""
    updated_at: str = ""
    language: str = "auto"
    user_preferences: UserPreferences = Field(default_factory=UserPreferences)
    conversation_history: list[ConversationTurn] = Field(default_factory=list)
    retrieved_skus: list[str] = Field(default_factory=list)
    last_query: str | None = None
    last_answer: str | None = None


class ChatSessionResponse(APIResponse):
    session: ChatSession


class ChatSessionListResponse(APIResponse):
    sessions: list[ChatSession] = Field(default_factory=list)
    total: int | None = None


class ConversationSummaryItem(BaseModel):
    """Server-side chat thread (maps to session_id); used by GET /chat/conversations."""

    id: str
    title: str
    created_at: str = ""
    updated_at: str = ""
    last_query: str | None = None
    message_count: int = 0


class ConversationListResponse(APIResponse):
    items: list[ConversationSummaryItem] = Field(default_factory=list)
    next_cursor: str | None = None


class ConversationMessageItem(BaseModel):
    role: str
    content: str
    timestamp: str = ""


class ConversationMessagesResponse(APIResponse):
    session_id: str
    messages: list[ConversationMessageItem] = Field(default_factory=list)
    next_cursor: str | None = None


class ChatRequest(BaseModel):
    session_id: str | None = None
    query: str
    top_k: int = 10
    temperature: float = 0.2
    filters: HybridSearchFilters = Field(default_factory=HybridSearchFilters)
    language: str | None = None
    # Optional location for weather-aware outfit recommendations (OpenWeatherMap)
    location_city: str | None = None
    location_lat: float | None = None
    location_lon: float | None = None


class ProductReasonSignal(BaseModel):
    type: str
    strength: str
    value: str | None = None


class ProductReasoning(BaseModel):
    short_reason: str = ""
    fallback: bool = False
    signals: list[ProductReasonSignal] = Field(default_factory=list)


class ProductCard(BaseModel):
    sku: str
    name: str
    image: str | None = None
    price: str | None = None
    currency: str = "EUR"
    availability: str = "In stock"
    url: str | None = None
    styling_reason: str = ""
    score: float | None = None
    # Optional semantic tags used for relevance / ranking (not required by clients)
    category: str | None = None
    is_accessory: bool | None = None
    is_occasion_specific: bool | None = None
    is_outfit_anchor: bool | None = None
    # For popular_products / best_sellers intent: why it's popular (e.g. "Ανήκει στα best sellers")
    popularity_reason: str = ""
    # Optional badge: "Best seller", "Trending", "Featured"
    badge: str = ""
    # Optional structured explanation for why this item was recommended
    reasoning: ProductReasoning | None = None


class ChatResponse(APIResponse):
    session_id: str
    query: str
    rewritten_query: str | None = None
    # Backwards-compat: 'answer' kept, 'message' is the preferred field name for UI text.
    answer: str
    message: str | None = None
    language: str = "el"
    sources: list[RAGSourceCitation] = Field(default_factory=list)
    products: list[ProductCard] = Field(default_factory=list)
    intent: str | None = None
    # Optional structured location/map payload (only set for location/store intents)
    location: dict | None = None
    map: dict | None = None
    metadata: dict | None = None


class ChatRecommendRequest(BaseModel):
    session_id: str | None = None
    query: str
    top_k: int = 10
    temperature: float = 0.3
    filters: HybridSearchFilters = Field(default_factory=HybridSearchFilters)
    language: str | None = None


class ChatRecommendResponse(APIResponse):
    session_id: str
    query: str
    answer: str
    language: str = "el"
    recommendations: list[RAGSourceCitation] = Field(default_factory=list)


class ChatCompareRequest(BaseModel):
    session_id: str | None = None
    skus: list[str] = Field(default_factory=list)
    query: str | None = None
    language: str | None = None
    temperature: float = 0.2


class ChatCompareResponse(APIResponse):
    session_id: str | None = None
    comparison: str
    language: str = "el"
    products: list[RAGSourceCitation] = Field(default_factory=list)


# ── Health ───────────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str = "ok"
    app: str = "MostyAPI"
    version: str = "2.0.0"
    timestamp: str = ""
    features: dict[str, bool] = Field(default_factory=dict)


# ── Admin: AI evaluations ───────────────────────────────────────────────────


class AIEvaluationRecord(BaseModel):
    id: int
    created_at: str
    test_query: str
    assistant_response: str
    detected_intent: str | None = None
    requested_category: str | None = None
    returned_categories: list[str] | None = None
    recommended_products: list[dict[str, Any]] | None = None
    recommended_skus: list[str] | None = None
    stock_validation_passed: bool = True
    duplicate_validation_passed: bool = True
    category_validation_passed: bool = True
    memory_isolation_passed: bool = True
    formatting_validation_passed: bool = True
    hallucination_validation_passed: bool = True
    intent_score: float | None = None
    category_score: float | None = None
    stock_score: float | None = None
    relevance_score: float | None = None
    styling_score: float | None = None
    memory_score: float | None = None
    format_score: float | None = None
    total_score: float | None = None
    fail_reasons: list[str] | None = None
    raw_judge_output: str | None = None
    latency_ms: int | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    evaluation_status: str = "pass"
    dataset_name: str | None = None


class AIEvalSummaryResponse(APIResponse):
    total_tests: int = 0
    average_total_score: float | None = None
    average_intent_score: float | None = None
    average_category_score: float | None = None
    average_stock_score: float | None = None
    average_relevance_score: float | None = None
    average_styling_score: float | None = None
    average_memory_score: float | None = None
    average_format_score: float | None = None
    average_latency_ms: float | None = None
    pass_count: int = 0
    warning_count: int = 0
    fail_count: int = 0
    fail_percentage: float | None = None
    most_common_fail_reasons: list[dict[str, Any]] = Field(default_factory=list)
    most_common_wrong_categories: list[dict[str, Any]] = Field(default_factory=list)
    most_common_out_of_stock: list[dict[str, Any]] = Field(default_factory=list)
    most_common_hallucinations: list[dict[str, Any]] = Field(default_factory=list)


class AIEvalListResponse(APIResponse):
    items: list[AIEvaluationRecord] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20
    total_pages: int = 0


class AIEvalRunRequest(BaseModel):
    test_queries: list[str] | None = None  # if None, use default benchmark set
    dataset_name: str | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    limit: int | None = None
    category_filter: str | None = None
    dry_run: bool = False


class AIEvalRunResponse(APIResponse):
    run_id: str | None = None
    total_run: int = 0
    saved_count: int = 0
    dry_run: bool = False
    summary: dict[str, Any] = Field(default_factory=dict)


# ── Commerce Features: Extended Schemas ──────────────────────────────────────


class UserProfileResponse(APIResponse):
    session_id: str
    profile: dict[str, Any] = Field(default_factory=dict)


class ContradictionCheckResponse(APIResponse):
    has_contradictions: bool = False
    contradictions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    original_filters: dict[str, Any] = Field(default_factory=dict)


class CompleteLookGroup(BaseModel):
    target_category: str
    items: list[dict[str, Any]] = Field(default_factory=list)


class CompleteLookResponse(APIResponse):
    anchor_sku: str
    anchor_name: str = ""
    anchor_categories: list[str] = Field(default_factory=list)
    groups: list[CompleteLookGroup] = Field(default_factory=list)
    message: str | None = None


class StructuredProductComparison(BaseModel):
    sku: str
    name: str
    price: float | None = None
    brand: str | None = None
    categories: list[str] = Field(default_factory=list)
    style_notes: str = ""
    occasion_fit: str = ""
    material_hints: str = ""
    color_hints: str = ""
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    best_for: str = ""
    value_for_money_note: str = ""


class StructuredCompareResponse(APIResponse):
    products: list[StructuredProductComparison] = Field(default_factory=list)
    verdict: str = ""


class AlternativesGroup(BaseModel):
    alt_type: str
    reason: str = ""
    items: list[dict[str, Any]] = Field(default_factory=list)


class AlternativesResponse(APIResponse):
    anchor_sku: str
    anchor_name: str = ""
    groups: list[AlternativesGroup] = Field(default_factory=list)


class AlertSchema(BaseModel):
    id: int
    session_id: str
    alert_type: str
    status: str = "active"
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    triggered_at: str | None = None


class AlertListResponse(APIResponse):
    alerts: list[AlertSchema] = Field(default_factory=list)


class MerchandisingRuleSchema(BaseModel):
    id: int
    rule_type: str
    value: str
    boost_weight: float = 0.1
    label: str | None = None
    active: bool = True
    starts_at: str | None = None
    ends_at: str | None = None
    created_at: str | None = None


class MerchandisingRuleListResponse(APIResponse):
    rules: list[MerchandisingRuleSchema] = Field(default_factory=list)


class AnalyticsSummaryResponse(APIResponse):
    top_events: list[dict[str, Any]] = Field(default_factory=list)
    top_feedback_actions: list[dict[str, Any]] = Field(default_factory=list)
    top_wishlist_skus: list[dict[str, Any]] = Field(default_factory=list)
    most_liked_skus: list[dict[str, Any]] = Field(default_factory=list)
    most_rejected_skus: list[dict[str, Any]] = Field(default_factory=list)
    no_result_count: int = 0
    unique_sessions: int = 0
    top_saved_look_occasions: list[dict[str, Any]] = Field(default_factory=list)
