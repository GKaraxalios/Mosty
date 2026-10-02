"""Detect refinement / novelty / variation requests for conversational shopping (additive)."""
from __future__ import annotations

from app.utils.commerce_semantics import fold_el
from app.utils.style_refinement import novelty_requested_from_query


def refinement_or_fresh_variation_requested(query: str) -> bool:
    """True if the user refines style/price/feel or asks for alternatives — prefer unseen SKUs."""
    if novelty_requested_from_query(query):
        return True
    q = fold_el(query or "")
    if not q:
        return False
    markers = (
        "πιο minimal",
        "πιο απλο",
        "πιο απλή",
        "πιο κομψο",
        "πιο sexy",
        "πιο οικονομικο",
        "πιο φθηνο",
        "πιο premium",
        "πιο βραδινο",
        "πιο εντυπωσιακο",
        "πιο elegant",
        "πιο ιδιαιτερο",
        "λιγοτερο βραδινο",
        "οχι τοσο βραδινο",
        "οχι τοσο glam",
        "πιο casual",
        "πιο χαλαρο",
        "ποικιλια",
        "αλλο στυλ",
        "αλλη επιλογη",
        "more minimal",
        "more casual",
        "more elegant",
        "cheaper",
        "affordable",
        "less formal",
        "less evening",
    )
    return any(m in q for m in markers)


def wants_exclude_already_shown_skus(
    *,
    user_query: str,
    has_conversation_history: bool,
    restrict_to_candidates: bool,
    repeated_user_message: bool,
    intent_novelty_request: bool | None = None,
    intent_refinement_request: bool | None = None,
) -> bool:
    """When True, prefer filtering out SKUs already shown in this session.

    LLM tri-state: if either novelty_request or refinement_request is True → exclude (when history).
    If both are explicitly False → do not exclude (overrides phrase heuristics).
    If either is None → fall back to refinement_or_fresh_variation_requested().
    """
    if restrict_to_candidates or not has_conversation_history:
        return False
    if repeated_user_message:
        return True
    if intent_novelty_request is True or intent_refinement_request is True:
        return True
    if intent_novelty_request is False and intent_refinement_request is False:
        return False
    return refinement_or_fresh_variation_requested(user_query)


def effective_penalize_last_shown_rerank(
    user_query: str,
    intent_novelty_request: bool | None,
    intent_refinement_request: bool | None,
) -> bool:
    """Whether to downrank SKUs from the previous turn (soft rerank layer)."""
    if intent_novelty_request is True or intent_refinement_request is True:
        return True
    if intent_novelty_request is False and intent_refinement_request is False:
        return False
    return refinement_or_fresh_variation_requested(user_query)


def is_repeated_user_message(current_query: str, last_query: str | None) -> bool:
    """Same substantive message as last turn (normalized)."""
    a = fold_el((current_query or "").strip())
    b = fold_el((last_query or "").strip())
    if not a or not b or len(a) < 4:
        return False
    return a == b
