"""In-memory TTL cache with namespace support.

Abstraction layer — replace the `MemoryBackend` with `RedisBackend` or any
other implementation by switching `CACHE_BACKEND` in settings.
"""
from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from threading import Lock
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# ── Backend interface ────────────────────────────────────────────────────────


class CacheBackend(ABC):
    @abstractmethod
    def get(self, key: str) -> Any | None: ...

    @abstractmethod
    def set(self, key: str, value: Any, ttl: int) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def delete_by_prefix(self, prefix: str) -> int: ...

    @abstractmethod
    def clear(self) -> None: ...

    @abstractmethod
    def stats(self) -> dict[str, Any]: ...


# ── In-memory implementation ─────────────────────────────────────────────────


class _CacheEntry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, ttl: int) -> None:
        self.value = value
        self.expires_at = time.monotonic() + ttl


class MemoryBackend(CacheBackend):
    def __init__(self) -> None:
        self._store: dict[str, _CacheEntry] = {}
        self._lock = Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            if time.monotonic() > entry.expires_at:
                del self._store[key]
                self._misses += 1
                return None
            self._hits += 1
            return entry.value

    def set(self, key: str, value: Any, ttl: int) -> None:
        with self._lock:
            self._store[key] = _CacheEntry(value, ttl)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def delete_by_prefix(self, prefix: str) -> int:
        with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                del self._store[k]
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            live = sum(1 for e in self._store.values() if now < e.expires_at)
            return {
                "backend": "memory",
                "total_keys": len(self._store),
                "live_keys": live,
                "hits": self._hits,
                "misses": self._misses,
            }


# ── Cache Service (singleton-like) ───────────────────────────────────────────


_backend: CacheBackend | None = None


def _get_backend() -> CacheBackend:
    global _backend
    if _backend is None:
        settings = get_settings()
        if settings.CACHE_BACKEND == "memory":
            _backend = MemoryBackend()
        else:
            logger.warning(
                "Unknown CACHE_BACKEND=%s — falling back to memory",
                settings.CACHE_BACKEND,
            )
            _backend = MemoryBackend()
    return _backend


# ── Namespace helpers ────────────────────────────────────────────────────────

NS_PRODUCTS = "products"
NS_PRODUCT_DETAIL = "product_detail"
NS_EMBEDDINGS = "embeddings"
NS_QUERY_EMBED = "query_embed"
NS_RAG_SEARCH = "rag_search"
NS_RAG_ANSWER = "rag_answer"


def _make_key(namespace: str, *parts: str) -> str:
    raw = ":".join([namespace] + list(parts))
    return raw


def _hash_key(namespace: str, data: str) -> str:
    h = hashlib.sha256(data.encode()).hexdigest()[:24]
    return f"{namespace}:{h}"


# ── Public API ───────────────────────────────────────────────────────────────


class CacheService:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._enabled = self._settings.CACHE_ENABLED
        self._backend = _get_backend()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def get(self, namespace: str, key: str) -> Any | None:
        if not self._enabled:
            return None
        full_key = _make_key(namespace, key)
        return self._backend.get(full_key)

    def get_hashed(self, namespace: str, raw_data: str) -> Any | None:
        if not self._enabled:
            return None
        full_key = _hash_key(namespace, raw_data)
        return self._backend.get(full_key)

    def set(self, namespace: str, key: str, value: Any, ttl: int | None = None) -> None:
        if not self._enabled:
            return
        ttl = ttl or self._settings.CACHE_DEFAULT_TTL
        full_key = _make_key(namespace, key)
        self._backend.set(full_key, value, ttl)

    def set_hashed(self, namespace: str, raw_data: str, value: Any, ttl: int | None = None) -> None:
        if not self._enabled:
            return
        ttl = ttl or self._settings.CACHE_DEFAULT_TTL
        full_key = _hash_key(namespace, raw_data)
        self._backend.set(full_key, value, ttl)

    def invalidate_namespace(self, namespace: str) -> int:
        if not self._enabled:
            return 0
        removed = self._backend.delete_by_prefix(namespace + ":")
        logger.info("Cache invalidated namespace=%s, removed=%d", namespace, removed)
        return removed

    def invalidate_product(self, sku: str) -> None:
        if not self._enabled:
            return
        self._backend.delete(_make_key(NS_PRODUCT_DETAIL, sku))
        self._backend.delete_by_prefix(f"{NS_PRODUCTS}:")

    def invalidate_all_products(self) -> None:
        if not self._enabled:
            return
        self.invalidate_namespace(NS_PRODUCTS)
        self.invalidate_namespace(NS_PRODUCT_DETAIL)
        self.invalidate_namespace(NS_RAG_SEARCH)
        self.invalidate_namespace(NS_RAG_ANSWER)
        logger.info("All product-related caches invalidated")

    def clear_all(self) -> None:
        self._backend.clear()
        logger.info("Full cache cleared")

    def stats(self) -> dict[str, Any]:
        return self._backend.stats()
