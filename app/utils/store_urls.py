"""Resolve storefront base URL and absolutize product image paths from Magento.

Magento GraphQL often returns ``image.url`` as a site-relative path (e.g. ``/media/catalog/...``)
or protocol-relative (``//cdn.example/...``). Chat UI previously required ``http`` prefix and
showed placeholders for valid relative URLs.
"""
from __future__ import annotations

from urllib.parse import urlparse

from app.core.config import get_settings


def origin_from_graphql_url(graphql_url: str) -> str:
    """``https://shop.example/graphql`` → ``https://shop.example``."""
    u = urlparse((graphql_url or "").strip())
    if u.scheme and u.netloc:
        return f"{u.scheme}://{u.netloc}".rstrip("/")
    return "https://mosty.com"


def get_storefront_origin() -> str:
    """Public origin for product pages and media. Override with STORE_PUBLIC_ORIGIN if GraphQL host ≠ storefront."""
    s = get_settings()
    custom = (getattr(s, "STORE_PUBLIC_ORIGIN", None) or "").strip()
    if custom:
        return custom.rstrip("/")
    return origin_from_graphql_url(s.MAGENTO_GRAPHQL_URL)


def absolutize_media_url(url: str | None, public_origin: str | None = None) -> str | None:
    """Turn relative / protocol-relative media URLs into absolute https URLs."""
    if url is None:
        return None
    s = str(url).strip()
    if not s or s.lower() in ("null", "none", "undefined"):
        return None
    if s.startswith(("http://", "https://")):
        return s
    if s.startswith("//"):
        return "https:" + s
    origin = (public_origin or get_storefront_origin()).rstrip("/")
    if s.startswith("/"):
        return origin + s
    return f"{origin}/{s.lstrip('/')}"
