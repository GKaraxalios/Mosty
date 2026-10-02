from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any

from app.models.schemas import (
    CategorySchema,
    ContentSchema,
    ImageSchema,
    PriceSchema,
    ProductSchema,
    RAGDocument,
)


_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_SPACE_RE = re.compile(r"\s+")


def strip_html(raw: str | None) -> str:
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = _MULTI_SPACE_RE.sub(" ", text).strip()
    return text


def to_iso8601(value: str | None) -> str | None:
    if not value:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return value


def _pick_canonical_url(product: dict[str, Any]) -> str | None:
    rewrites = product.get("url_rewrites") or []
    if rewrites:
        return f"https://mosty.com/{rewrites[0].get('url', '')}"
    url_key = product.get("url_key")
    if url_key:
        return f"https://mosty.com/{url_key}"
    return None


def _all_urls(product: dict[str, Any]) -> list[str]:
    rewrites = product.get("url_rewrites") or []
    return [f"https://mosty.com/{rw['url']}" for rw in rewrites if rw.get("url")]


def _extract_price(product: dict[str, Any]) -> PriceSchema:
    pr = product.get("price_range") or {}
    mp = pr.get("minimum_price") or {}
    rp = mp.get("regular_price") or {}
    fp = mp.get("final_price") or {}
    disc = mp.get("discount") or {}

    regular = rp.get("value")
    final = fp.get("value")
    currency = rp.get("currency") or fp.get("currency") or "EUR"

    discount_pct = disc.get("percent_off") or 0.0
    if not discount_pct and regular and final and regular > final:
        discount_pct = round((1 - final / regular) * 100, 1)

    return PriceSchema(
        currency=currency,
        regular=regular,
        sale=final if final and regular and final < regular else None,
        current=final or regular,
        discount_percent=discount_pct if discount_pct else None,
    )


def _extract_categories(product: dict[str, Any]) -> list[CategorySchema]:
    cats = product.get("categories") or []
    result: list[CategorySchema] = []
    for c in cats:
        name = c.get("name", "")
        url_path = c.get("url_path") or ""
        slug = url_path.split("/")[-1] if url_path else None
        result.append(CategorySchema(name=name, slug=slug))
    return result


def _extract_images(product: dict[str, Any]) -> list[ImageSchema]:
    from app.utils.store_urls import absolutize_media_url, get_storefront_origin

    origin = get_storefront_origin()
    images: list[ImageSchema] = []
    img = product.get("image") or {}
    if img.get("url"):
        abs_u = absolutize_media_url(img["url"], origin)
        if abs_u:
            images.append(ImageSchema(url=abs_u, label=img.get("label")))
    for mi in product.get("media_gallery") or []:
        url = mi.get("url")
        abs_u = absolutize_media_url(url, origin)
        if abs_u and abs_u not in [i.url for i in images]:
            images.append(ImageSchema(url=abs_u, label=mi.get("label")))
    return images


def normalize_product(raw: dict[str, Any]) -> ProductSchema:
    desc_html = ""
    desc_obj = raw.get("description") or {}
    if isinstance(desc_obj, dict):
        desc_html = desc_obj.get("html") or ""
    elif isinstance(desc_obj, str):
        desc_html = desc_obj
    desc_text = strip_html(desc_html)

    short_desc = ""
    short_obj = raw.get("short_description") or {}
    if isinstance(short_obj, dict):
        short_desc = short_obj.get("html") or ""
    elif isinstance(short_obj, str):
        short_desc = short_obj
    short_text = strip_html(short_desc)

    return ProductSchema(
        sku=raw.get("sku", ""),
        name=raw.get("name", ""),
        type=raw.get("__typename"),
        url=_pick_canonical_url(raw),
        price=_extract_price(raw),
        categories=_extract_categories(raw),
        images=_extract_images(raw),
        content=ContentSchema(
            short=short_text or None,
            long=desc_html or None,
            text=desc_text or None,
        ),
        meta_keyword=raw.get("meta_keyword"),
        meta_description=raw.get("meta_description"),
        created_at=to_iso8601(raw.get("created_at")),
        updated_at=to_iso8601(raw.get("updated_at")),
    )


def product_to_rag_document(product: ProductSchema, stock_status: str | None = None) -> RAGDocument:
    return RAGDocument(
        sku=product.sku,
        name=product.name,
        type=product.type,
        canonical_url=product.url,
        all_urls=[product.url] if product.url else [],
        description_text=product.content.text,
        description_html=product.content.long,
        meta_keyword=product.meta_keyword,
        meta_description=product.meta_description,
        categories=[c.name for c in product.categories],
        image_url=product.images[0].url if product.images else None,
        currency=product.price.currency,
        regular_price=product.price.regular,
        final_price=product.price.current,
        discount_percent=product.price.discount_percent,
        created_at=product.created_at,
        updated_at=product.updated_at,
        stock_status=stock_status,
    )


def rag_document_to_text(doc: RAGDocument) -> str:
    lines = [
        f"Product Name: {doc.name}",
        f"SKU: {doc.sku}",
        f"Type: {doc.type or 'N/A'}",
        f"Category: {', '.join(doc.categories) if doc.categories else 'N/A'}",
    ]
    if doc.description_text:
        lines.append(f"Description: {doc.description_text}")
    if doc.meta_description:
        lines.append(f"Meta description: {doc.meta_description}")
    if doc.meta_keyword:
        lines.append(f"Keywords: {doc.meta_keyword}")

    price_parts: list[str] = []
    if doc.final_price is not None:
        price_parts.append(f"{doc.final_price} {doc.currency}")
    if doc.regular_price and doc.regular_price != doc.final_price:
        price_parts.append(f"(was {doc.regular_price} {doc.currency})")
    if doc.discount_percent:
        price_parts.append(f"-{doc.discount_percent}%")
    if price_parts:
        lines.append(f"Price: {' '.join(price_parts)}")

    if doc.canonical_url:
        lines.append(f"URL: {doc.canonical_url}")
    return "\n".join(lines)


def _attr_from_configurable(raw: dict[str, Any], code: str) -> str | None:
    """Get first value for attribute code from configurable_options (e.g. color, material)."""
    for opt in raw.get("configurable_options") or []:
        if (opt.get("attribute_code") or "").lower() == code.lower():
            vals = opt.get("values") or []
            if vals:
                return vals[0].get("label") or vals[0].get("value_index")
    return None


def build_enriched_product_text(
    doc: RAGDocument,
    raw_product: dict[str, Any] | None = None,
) -> str:
    """Build structured product text for fashion retrieval and stylistic matching.
    Includes Brand, Color, Fabric, Style, Subcategory, Availability. Uses raw_product
    for configurable_options (color, material, etc.) when available.
    """
    lines: list[str] = []
    lines.append(f"Product Name: {doc.name}")
    lines.append(f"SKU: {doc.sku}")
    # Category / Subcategory: use first as category, rest as subcategory
    if doc.categories:
        lines.append(f"Category: {doc.categories[0]}")
        if len(doc.categories) > 1:
            lines.append(f"Subcategory: {', '.join(doc.categories[1:])}")
    else:
        lines.append("Category: N/A")
        lines.append("Subcategory: N/A")
    # Brand / designer from configurable or meta
    brand = None
    if raw_product:
        brand = _attr_from_configurable(raw_product, "brand") or _attr_from_configurable(
            raw_product, "designer"
        )
    if not brand and doc.meta_keyword:
        brand = doc.meta_keyword.split(",")[0].strip() if doc.meta_keyword else None
    lines.append(f"Brand: {brand or 'N/A'}")
    # Color, Fabric, Style from configurable_options
    if raw_product:
        color = _attr_from_configurable(raw_product, "color")
        if color:
            lines.append(f"Color: {color}")
        fabric = _attr_from_configurable(raw_product, "material") or _attr_from_configurable(
            raw_product, "fabric"
        )
        if fabric:
            lines.append(f"Fabric: {fabric}")
        style = _attr_from_configurable(raw_product, "style")
        if style:
            lines.append(f"Style: {style}")
    if "Color:" not in "\n".join(lines):
        lines.append("Color: N/A")
    if "Fabric:" not in "\n".join(lines):
        lines.append("Fabric: N/A")
    if "Style:" not in "\n".join(lines):
        lines.append("Style: N/A")
    # Price
    price_parts: list[str] = []
    if doc.final_price is not None:
        price_parts.append(f"{doc.final_price} {doc.currency}")
    if doc.regular_price and doc.regular_price != doc.final_price:
        price_parts.append(f"(was {doc.regular_price} {doc.currency})")
    if doc.discount_percent:
        price_parts.append(f"-{doc.discount_percent}%")
    lines.append(f"Price: {' '.join(price_parts)}" if price_parts else "Price: N/A")
    # Availability
    availability = "In stock" if (doc.stock_status or "").upper() == "IN_STOCK" else "Out of stock"
    lines.append(f"Availability: {availability}")
    # Description
    if doc.description_text:
        lines.append(f"Description: {doc.description_text}")
    elif doc.meta_description:
        lines.append(f"Description: {doc.meta_description}")
    if doc.meta_keyword:
        lines.append(f"Keywords: {doc.meta_keyword}")
    if doc.canonical_url:
        lines.append(f"URL: {doc.canonical_url}")
    return "\n".join(lines)
