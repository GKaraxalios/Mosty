"""Structured product comparison service.

Builds a detailed, structured comparison for a set of SKUs using product
metadata, heuristic extraction of brand/material/color/style from text, and
deterministic pros/cons generation.
"""
from __future__ import annotations

import re
from typing import Any

from app.core.logging import get_logger
from app.models.schemas import RAGChunk
from app.services.rag_ingestion_service import get_chunk_store

logger = get_logger(__name__)

_MATERIAL_KEYWORDS = [
    "cotton", "βαμβάκι", "βαμβακερό", "leather", "δέρμα", "δερμάτινο",
    "silk", "μετάξι", "μεταξωτό", "polyester", "πολυεστέρας", "wool",
    "μαλλί", "μάλλινο", "linen", "λινό", "denim", "τζιν", "suede",
    "σουέτ", "nylon", "νάιλον", "viscose", "βισκόζη", "cashmere",
    "κασμίρ", "satin", "σατέν", "velvet", "βελούδο", "chiffon", "σιφόν",
]

_COLOR_KEYWORDS = [
    "μαύρο", "λευκό", "κόκκινο", "μπλε", "πράσινο", "κίτρινο", "ροζ",
    "μπεζ", "γκρι", "καφέ", "μωβ", "πορτοκαλί", "εκρού", "χρυσό",
    "ασημί", "μπορντό", "navy", "black", "white", "red", "blue",
    "green", "yellow", "pink", "beige", "grey", "gray", "brown",
    "purple", "orange", "gold", "silver", "burgundy", "ivory", "cream",
]

_OCCASION_KEYWORDS = [
    "casual", "επαγγελματικό", "βραδινό", "formal", "office", "γραφείο",
    "party", "πάρτι", "γάμο", "wedding", "everyday", "καθημερινό",
    "sport", "αθλητικό", "beach", "παραλία", "date", "ραντεβού",
]

_STYLE_KEYWORDS = [
    "minimal", "μίνιμαλ", "classic", "κλασικό", "boho", "bohemian",
    "elegant", "κομψό", "sporty", "αθλητικό", "vintage", "modern",
    "μοντέρνο", "romantic", "ρομαντικό", "streetwear", "chic", "trendy",
]


class StructuredCompareService:

    def compare(self, skus: list[str]) -> dict[str, Any]:
        store = get_chunk_store()
        comparisons: list[dict[str, Any]] = []

        for sku in skus:
            chunk = _find_chunk(sku, store)
            if chunk:
                comparisons.append(self._build_comparison(chunk))
            else:
                comparisons.append({
                    "sku": sku, "name": "Not found", "price": None,
                    "brand": None, "categories": [], "style_notes": "",
                    "occasion_fit": "", "material_hints": "", "color_hints": "",
                    "pros": [], "cons": ["Δεν βρέθηκε στον κατάλογο"],
                    "best_for": "", "value_for_money_note": "",
                })

        verdict = self._build_verdict(comparisons) if len(comparisons) >= 2 else ""
        return {"products": comparisons, "verdict": verdict}

    def _build_comparison(self, chunk: RAGChunk) -> dict[str, Any]:
        haystack = " ".join(filter(None, [
            chunk.text, chunk.description_text, chunk.meta_keyword,
            chunk.meta_description, chunk.name,
        ])).lower()

        brand = _extract_brand(chunk)
        materials = [m for m in _MATERIAL_KEYWORDS if m.lower() in haystack]
        colors = [c for c in _COLOR_KEYWORDS if c.lower() in haystack]
        occasions = [o for o in _OCCASION_KEYWORDS if o.lower() in haystack]
        styles = [s for s in _STYLE_KEYWORDS if s.lower() in haystack]

        pros: list[str] = []
        cons: list[str] = []

        if chunk.price is not None:
            if chunk.price < 50:
                pros.append("Προσιτή τιμή")
            elif chunk.price > 200:
                cons.append("Premium τιμή")

        if chunk.stock_status and "IN_STOCK" in chunk.stock_status.upper():
            pros.append("Διαθέσιμο")
        elif chunk.stock_status and "OUT" in chunk.stock_status.upper():
            cons.append("Εξαντλημένο")

        if materials:
            pros.append(f"Υλικό: {', '.join(materials[:3])}")

        if chunk.image_url:
            pros.append("Διαθέσιμη εικόνα")
        else:
            cons.append("Χωρίς εικόνα")

        if brand:
            pros.append(f"Brand: {brand}")

        best_for = ", ".join(occasions[:2]) if occasions else "Γενικής χρήσης"

        vfm = ""
        if chunk.price is not None:
            if chunk.price < 30:
                vfm = "Εξαιρετική σχέση ποιότητας-τιμής"
            elif chunk.price < 80:
                vfm = "Καλή σχέση ποιότητας-τιμής"
            elif chunk.price < 150:
                vfm = "Μέτρια τιμή — ελέγξτε ποιότητα υλικών"
            else:
                vfm = "Premium positioning — αναμένεται υψηλή ποιότητα"

        return {
            "sku": chunk.sku,
            "name": chunk.name,
            "price": chunk.price,
            "brand": brand,
            "categories": chunk.categories or [],
            "style_notes": ", ".join(styles[:3]) if styles else "Δεν αναγνωρίστηκε συγκεκριμένο style",
            "occasion_fit": ", ".join(occasions[:3]) if occasions else "Γενικής χρήσης",
            "material_hints": ", ".join(materials[:3]) if materials else "Δεν αναφέρεται",
            "color_hints": ", ".join(colors[:3]) if colors else "Δεν αναφέρεται",
            "pros": pros,
            "cons": cons,
            "best_for": best_for,
            "value_for_money_note": vfm,
        }

    @staticmethod
    def _build_verdict(products: list[dict[str, Any]]) -> str:
        valid = [p for p in products if p.get("price") is not None and p.get("name") != "Not found"]
        if len(valid) < 2:
            return "Δεν υπάρχουν αρκετά προϊόντα για σύγκριση."

        cheapest = min(valid, key=lambda p: p["price"])
        most_expensive = max(valid, key=lambda p: p["price"])
        most_pros = max(valid, key=lambda p: len(p.get("pros", [])))

        parts: list[str] = []
        if cheapest["sku"] != most_expensive["sku"]:
            parts.append(
                f"Πιο οικονομικό: {cheapest['name']} ({cheapest['price']}€)"
            )
            parts.append(
                f"Πιο premium: {most_expensive['name']} ({most_expensive['price']}€)"
            )
        parts.append(
            f"Περισσότερα πλεονεκτήματα: {most_pros['name']}"
        )
        return " | ".join(parts)


def _find_chunk(sku: str, store: dict[str, RAGChunk]) -> RAGChunk | None:
    for c in store.values():
        if c.sku == sku:
            return c
    return None


def _extract_brand(chunk: RAGChunk) -> str | None:
    kw = chunk.meta_keyword or ""
    if kw:
        parts = [p.strip() for p in kw.split(",") if p.strip()]
        if parts:
            return parts[0]
    name_parts = chunk.name.split()
    if name_parts:
        candidate = name_parts[0]
        if len(candidate) >= 2 and not re.match(r"^\d", candidate):
            return candidate
    return None
