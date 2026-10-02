from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import computed_field, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    APP_ENV: Literal["local", "production"] = "local"

    API_V1_PREFIX: str = "/api/v1"
    MOSTY_RAG_API_KEY: str = ""
    ADMIN_API_KEY: str = ""  # Required for /admin/* endpoints; keep separate from API key

    MAGENTO_GRAPHQL_URL: str = "https://mosty.com/graphql"
    MAGENTO_STORE_CODE: str = "default"
    MAGENTO_PAGE_SIZE: int = 50
    MAGENTO_REQUEST_TIMEOUT: float = 30.0
    MAGENTO_ACCESS_TOKEN: str = ""

    GOOGLE_CLOUD_PROJECT: str = ""
    GOOGLE_CLOUD_LOCATION: str = "europe-west1"
    GOOGLE_APPLICATION_CREDENTIALS: str = ""

    VERTEX_EMBEDDING_MODEL: str = "text-embedding-005"
    VERTEX_GENERATIVE_MODEL: str = "gemini-2.5-flash"
    VERTEX_INDEX_ID: str = ""
    VERTEX_INDEX_ENDPOINT_ID: str = ""
    VERTEX_DEPLOYED_INDEX_ID: str = ""
    VERTEX_RAG_TOP_K: int = 5

    CHUNK_MAX_TOKENS: int = 512
    CHUNK_OVERLAP_TOKENS: int = 64

    ENABLE_SCHEDULER: bool = False
    FULL_SYNC_CRON: str = "0 3 * * *"
    INCREMENTAL_SYNC_INTERVAL_MINUTES: int = 30
    INCREMENTAL_SYNC_ENABLED: bool = True

    SYNC_STATE_PATH: str = "data/sync_state.json"
    CHUNK_STORE_PATH: str = "data/chunk_store.json"
    INDEX_BATCH_SIZE: int = 20
    INDEX_LOCK_PATH: str = "data/.index_sync.lock"
    INDEX_LOCK_TIMEOUT_SECONDS: int = 3600
    GCS_STATE_BUCKET: str = ""

    CACHE_ENABLED: bool = True
    CACHE_BACKEND: str = "memory"
    CACHE_DEFAULT_TTL: int = 300
    CACHE_PRODUCTS_TTL: int = 600
    CACHE_RAG_SEARCH_TTL: int = 120
    CACHE_RAG_ANSWER_TTL: int = 60

    HYBRID_DENSE_WEIGHT: float = 0.7
    HYBRID_SPARSE_WEIGHT: float = 0.3

    RERANK_ENABLED: bool = True
    # Candidates retrieved from hybrid merge before rerank / dedupe (raise recall).
    RERANK_TOP_N: int = 40
    FINAL_TOP_K: int = 10
    # Floor for rerank output in chat commerce so low client top_k does not collapse to 1–2 SKUs.
    COMMERCE_RERANK_FLOOR: int = 8
    # Target minimum SKUs before triggering relaxed / semantic fallback (when catalog allows).
    COMMERCE_RETRIEVAL_MIN_RESULTS: int = 5
    # When category+color metadata yields too few chunks, re-query with color dropped from
    # hybrid pre-filter only; post_filter + finalize still enforce color (fixes thin first screens).
    COMMERCE_COLOR_RECALL_ENABLED: bool = True
    COMMERCE_COLOR_RECALL_TRIGGER_MIN: int = 8
    # Multi-level fallback: drop soft metadata filters, then semantic-only, then category-only.
    COMMERCE_RELAXED_FALLBACK_ENABLED: bool = True
    # When target category is strict, do not run unfiltered / budget-only hybrid passes
    # (they surface unrelated categories e.g. jewelry for "dress" queries).
    COMMERCE_SKIP_BROAD_FALLBACK_WHEN_CATEGORY_STRICT: bool = True
    # Final pass: drop SKUs that do not match target_category (same rules as hybrid category filter).
    COMMERCE_ENFORCE_TARGET_CATEGORY_FILTER: bool = True
    # Dense neighbor count scales with this (see hybrid_search_service fetch_n).
    HYBRID_DENSE_NEIGHBOR_MIN: int = 80

    MEMORY_ENABLED: bool = True
    MEMORY_TTL_MINUTES: int = 60
    DEFAULT_ASSISTANT_LANGUAGE: str = "auto"
    MEMORY_BACKEND: str = "memory"

    # ── DB settings from env ────────────────────────────────────────────────
    DB_HOST_LOCAL: str = Field(default="127.0.0.1")
    DB_PORT_LOCAL: int = Field(default=3306)
    DB_NAME_LOCAL: str = Field(default="mosty")
    DB_USER_LOCAL: str = Field(default="")
    DB_PASSWORD_LOCAL: str = Field(default="")

    DB_HOST_PRODUCTION: str = Field(default="127.0.0.1")
    DB_PORT_PRODUCTION: int = Field(default=3306)
    DB_NAME_PRODUCTION: str = Field(default="mosty")
    DB_USER_PRODUCTION: str = Field(default="")
    DB_PASSWORD_PRODUCTION: str = Field(default="")

    DB_CHARSET: str = "utf8mb4"
    DATABASE_URL_LOCAL: str | None = None
    DATABASE_URL_PRODUCTION: str | None = None

    OPENMETEO_BASE_URL: str = "https://api.open-meteo.com/v1"
    OPENMETEO_GEOCODING_URL: str = "https://geocoding-api.open-meteo.com/v1"
    GOOGLE_CLOUD_PROJECT_NUMBER: str = ""
    OPENMETEO_TIMEOUT: float = 10.0

    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_DEFAULT: str = "120/minute"
    RATE_LIMIT_AI: str = "30/minute"
    RATE_LIMIT_SYNC: str = "5/minute"

    CORS_ORIGINS: str = "*"
    CORS_ALLOW_METHODS: str = "*"
    CORS_ALLOW_HEADERS: str = "*"
    CORS_ALLOW_CREDENTIALS: bool = False

    PORT: int = 8080

    CHAT_MAX_INPUT_CHARS: int = 8000
    CHAT_ASK_TIMEOUT_SECONDS: float = 300.0
    CHAT_ATTACHMENT_MAX_BYTES: int = 5 * 1024 * 1024
    CHAT_ATTACHMENT_TTL_SECONDS: int = 3600
    # When True, /chat hides the API key field (server-injected key or future session auth).
    CHAT_HIDE_API_KEY_UI: bool = False

    # Chat: optional embedded map preview for addresses (no API key in browser; embed uses public URLs).
    CHAT_ENABLE_MAP_PREVIEW: bool = True
    CHAT_MAPS_OPEN_IN_NEW_TAB: bool = True

    APP_NAME: str = "MostyAPI"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = False
    LOG_STYLIST_PROMPT: bool = True

    # When True: ignore category/color/occasion/price filters (retrieval becomes query-only; wrong category/color often).
    # Default False so intent + request filters narrow the catalog (e.g. red dresses vs earrings).
    COMMERCE_SEARCH_FILTERS_DISABLED: bool = False

    # If metadata filters exclude every chunk, retry hybrid/BM25 on the full chunk store (query still drives ranking).
    # Prevents always-empty retrieval when category labels in RAG do not match intent Greek strings.
    HYBRID_FALLBACK_WHEN_NO_ELIGIBLE: bool = True
    # When a category filter is set, do NOT expand to the full catalog (avoids jewelry etc. for dress queries).
    HYBRID_SKIP_FULL_CATALOG_FALLBACK_WHEN_CATEGORY_FILTER: bool = True

    @computed_field
    @property
    def is_local(self) -> bool:
        return self.APP_ENV == "local"

    @computed_field
    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @computed_field
    @property
    def DB_HOST(self) -> str:
        return self.DB_HOST_LOCAL if self.is_local else self.DB_HOST_PRODUCTION

    @computed_field
    @property
    def DB_PORT(self) -> int:
        return self.DB_PORT_LOCAL if self.is_local else self.DB_PORT_PRODUCTION

    @computed_field
    @property
    def DB_NAME(self) -> str:
        return self.DB_NAME_LOCAL if self.is_local else self.DB_NAME_PRODUCTION

    @computed_field
    @property
    def DB_USER(self) -> str:
        return self.DB_USER_LOCAL if self.is_local else self.DB_USER_PRODUCTION

    @computed_field
    @property
    def DB_PASSWORD(self) -> str:
        return self.DB_PASSWORD_LOCAL if self.is_local else self.DB_PASSWORD_PRODUCTION

    @computed_field
    @property
    def DATABASE_URL(self) -> str | None:
        if self.is_local and self.DATABASE_URL_LOCAL and self.DATABASE_URL_LOCAL.strip():
            return self.DATABASE_URL_LOCAL.strip()

        if self.is_production and self.DATABASE_URL_PRODUCTION and self.DATABASE_URL_PRODUCTION.strip():
            return self.DATABASE_URL_PRODUCTION.strip()

        return None

    model_config = SettingsConfigDict(
        env_file=(BASE_DIR / ".env", BASE_DIR / ".env.local"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    return Settings()