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

    # /chat UI (server-side template injection + bootstrap JSON)
    CHAT_HIDE_API_KEY_UI: bool = False
    CHAT_MAX_INPUT_CHARS: int = 8000
    CHAT_ENABLE_MAP_PREVIEW: bool = True
    CHAT_MAPS_OPEN_IN_NEW_TAB: bool = True

    MAGENTO_GRAPHQL_URL: str = "https://mosty.com/graphql"
    # If empty, derived from MAGENTO_GRAPHQL_URL origin (for absolutizing /media/... image URLs).
    STORE_PUBLIC_ORIGIN: str = ""
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
    # Deprecated for chat/ask: use HYBRID_RELAX_ON_EMPTY instead (kept for backwards compatibility).
    HYBRID_RELAX_CATEGORY_ON_EMPTY: bool = False
    # When True (default), if retrieval returns no usable results, retry with relaxed filters:
    # drop color → drop material/occasion/fit/style/season → drop category → drop size.
    HYBRID_RELAX_ON_EMPTY: bool = True

    RERANK_ENABLED: bool = True
    RERANK_TOP_N: int = 20
    FINAL_TOP_K: int = 10

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
    OPENMETEO_TIMEOUT: float = 10.0

    APP_NAME: str = "MostyAPI"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = False
    LOG_STYLIST_PROMPT: bool = False

    # ── Logging (file + console via app.core.logging.setup_logging) ─────────
    LOG_TO_FILE: bool = True
    LOG_FILE_PATH: str = "logs/app.log"
    LOG_FILE_MAX_BYTES: int = 10 * 1024 * 1024
    LOG_FILE_BACKUP_COUNT: int = 5
    LOG_ERROR_TO_SEPARATE_FILE: bool = True
    LOG_ERROR_FILE_PATH: str = "logs/errors.log"
    LOG_ERROR_FILE_MAX_BYTES: int = 10 * 1024 * 1024
    LOG_ERROR_FILE_BACKUP_COUNT: int = 5
    # Empty = derive from DEBUG (DEBUG if True, else INFO). Else: DEBUG, INFO, WARNING, ERROR.
    LOG_LEVEL: str = ""

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