"""Contradiction / impossible-filter detection for search queries.

Detects logical contradictions, impossible constraints and conflicting filters
before they reach the search pipeline. Returns user-safe explanations in Greek
and fallback suggestions.
"""
from __future__ import annotations

import re
from typing import Any

from app.core.logging import get_logger
from app.models.schemas import HybridSearchFilters

logger = get_logger(__name__)


class ContradictionService:

    def check(
        self,
        filters: HybridSearchFilters | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        contradictions: list[str] = []
        warnings: list[str] = []
        suggestions: list[str] = []

        if filters:
            self._check_budget(filters, contradictions, warnings, suggestions)
            self._check_category_vs_query(filters, query, contradictions, suggestions)
            self._check_color_vs_query(filters, query, warnings, suggestions)
            self._check_size_vs_query(filters, query, warnings, suggestions)

        if query:
            self._check_query_self_contradictions(query, contradictions, suggestions)
            self._check_query_budget_contradictions(query, contradictions, suggestions)

        return {
            "has_contradictions": len(contradictions) > 0,
            "contradictions": contradictions,
            "warnings": warnings,
            "suggestions": suggestions,
            "original_filters": filters.model_dump() if filters else {},
        }

    # ── Budget ────────────────────────────────────────────────────────────

    @staticmethod
    def _check_budget(
        f: HybridSearchFilters,
        contras: list[str],
        warns: list[str],
        suggs: list[str],
    ) -> None:
        if f.min_price is not None and f.max_price is not None:
            if f.min_price > f.max_price:
                contras.append(
                    f"Αντίφαση budget: min_price ({f.min_price}€) > max_price ({f.max_price}€)."
                )
                suggs.append(
                    "Αντιστρέψτε τα min/max ή χρησιμοποιήστε μόνο ένα price filter."
                )
            elif f.min_price == f.max_price:
                warns.append(
                    f"min_price == max_price ({f.min_price}€) — πολύ στενό εύρος τιμής."
                )

    # ── Category vs query ─────────────────────────────────────────────────

    @staticmethod
    def _check_category_vs_query(
        f: HybridSearchFilters,
        query: str | None,
        contras: list[str],
        suggs: list[str],
    ) -> None:
        if not f.category or not query:
            return
        cat_lower = f.category.lower().strip()
        q_lower = query.lower()
        negation_patterns = [
            f"χωρίς {cat_lower}", f"εκτός {cat_lower}", f"not {cat_lower}",
            f"no {cat_lower}", f"without {cat_lower}", f"όχι {cat_lower}",
        ]
        for pattern in negation_patterns:
            if pattern in q_lower:
                contras.append(
                    f"Ζητάτε '{f.category}' ως category filter αλλά "
                    f"στο query αναφέρετε '{pattern}'."
                )
                suggs.append("Αφαιρέστε το category filter ή αλλάξτε το query.")
                break

    # ── Color vs query ────────────────────────────────────────────────────

    @staticmethod
    def _check_color_vs_query(
        f: HybridSearchFilters,
        query: str | None,
        warns: list[str],
        suggs: list[str],
    ) -> None:
        if not f.color or not query:
            return
        color_lower = f.color.lower()
        q_lower = query.lower()
        anti = [f"χωρίς {color_lower}", f"not {color_lower}", f"no {color_lower}", f"όχι {color_lower}"]
        if any(p in q_lower for p in anti):
            warns.append(f"Αντίφαση: color filter '{f.color}' αλλά query αποκλείει αυτό το χρώμα.")
            suggs.append("Αφαιρέστε το color filter.")

    # ── Size vs query ─────────────────────────────────────────────────────

    @staticmethod
    def _check_size_vs_query(
        f: HybridSearchFilters,
        query: str | None,
        warns: list[str],
        suggs: list[str],
    ) -> None:
        if not f.size or not query:
            return
        size_lower = f.size.lower()
        q_lower = query.lower()
        anti = [f"χωρίς {size_lower}", f"not {size_lower}", f"όχι {size_lower}"]
        if any(p in q_lower for p in anti):
            warns.append(f"Αντίφαση: size filter '{f.size}' αλλά query αποκλείει αυτό το μέγεθος.")
            suggs.append("Αφαιρέστε το size filter.")

    # ── Self-contradictions in query text ─────────────────────────────────

    @staticmethod
    def _check_query_self_contradictions(
        query: str,
        contras: list[str],
        suggs: list[str],
    ) -> None:
        q_lower = query.lower()

        match = re.search(r"θέλω\s+(\S+).{1,30}χωρίς\s+\1", q_lower)
        if match:
            word = match.group(1)
            contras.append(f"Αντίφαση: 'θέλω {word}' και 'χωρίς {word}' στο ίδιο query.")
            suggs.append("Διευκρινίστε αν θέλετε ή δεν θέλετε αυτή την κατηγορία.")

        match_en = re.search(r"(?:want|need|looking for)\s+(\S+).{1,30}(?:without|no|not)\s+\1", q_lower)
        if match_en:
            word = match_en.group(1)
            contras.append(f"Contradiction: asking for '{word}' and 'without {word}' in the same query.")
            suggs.append("Clarify whether you want or don't want this category.")

    # ── Budget contradictions in query text ───────────────────────────────

    @staticmethod
    def _check_query_budget_contradictions(
        query: str,
        contras: list[str],
        suggs: list[str],
    ) -> None:
        q_lower = query.lower()
        under_match = re.search(r"(?:κάτω|under|below|less than|φθηνότερ)\s+(?:από\s+)?(\d+)", q_lower)
        min_match = re.search(r"(?:πάνω|above|over|more than|min(?:imum)?)\s+(?:από\s+)?(\d+)", q_lower)
        if under_match and min_match:
            under_val = float(under_match.group(1))
            min_val = float(min_match.group(1))
            if min_val > under_val:
                contras.append(
                    f"Αντίφαση budget στο query: ζητάτε πάνω από {min_val}€ "
                    f"αλλά και κάτω από {under_val}€."
                )
                suggs.append("Καθορίστε μία σαφή κλίμακα τιμών.")
