"""Tests for refinement detection and shown-SKU exclusion policy."""
from app.models.schemas import RAGSearchResult
from app.services.conversation_shopping_postfilter import exclude_already_shown_skus
from app.utils.conversation_refinement import (
    is_repeated_user_message,
    refinement_or_fresh_variation_requested,
    wants_exclude_already_shown_skus,
)


def test_refinement_phrases():
    assert refinement_or_fresh_variation_requested("Θέλω κάτι πιο minimal") is True
    assert refinement_or_fresh_variation_requested("μαύρα φορέματα") is False


def test_wants_exclude_requires_history():
    assert (
        wants_exclude_already_shown_skus(
            user_query="κάτι πιο minimal",
            has_conversation_history=False,
            restrict_to_candidates=False,
            repeated_user_message=False,
        )
        is False
    )
    assert (
        wants_exclude_already_shown_skus(
            user_query="κάτι πιο minimal",
            has_conversation_history=True,
            restrict_to_candidates=False,
            repeated_user_message=False,
        )
        is True
    )


def test_restrict_disables_exclude():
    assert (
        wants_exclude_already_shown_skus(
            user_query="από τις επιλογές κάτι άλλο",
            has_conversation_history=True,
            restrict_to_candidates=True,
            repeated_user_message=False,
        )
        is False
    )


def test_repeated_message_forces_exclude():
    assert (
        wants_exclude_already_shown_skus(
            user_query="ίδιο",
            has_conversation_history=True,
            restrict_to_candidates=False,
            repeated_user_message=True,
        )
        is True
    )


def test_llm_novelty_true_without_phrase():
    assert (
        wants_exclude_already_shown_skus(
            user_query="συνέχεια",
            has_conversation_history=True,
            restrict_to_candidates=False,
            repeated_user_message=False,
            intent_novelty_request=True,
            intent_refinement_request=None,
        )
        is True
    )


def test_llm_both_false_overrides_heuristic():
    assert (
        wants_exclude_already_shown_skus(
            user_query="κάτι πιο minimal",
            has_conversation_history=True,
            restrict_to_candidates=False,
            repeated_user_message=False,
            intent_novelty_request=False,
            intent_refinement_request=False,
        )
        is False
    )


def test_is_repeated_message():
    assert is_repeated_user_message("φορέματα", "φορέματα") is True
    assert is_repeated_user_message("άλλο", "φορέματα") is False


def test_exclude_already_shown_filters():
    a = RAGSearchResult(
        score=1.0,
        sku="OLD",
        name="",
        chunk_text="",
        url="https://x.example/p",
        metadata={"stock_status": "IN_STOCK"},
    )
    b = RAGSearchResult(
        score=0.9,
        sku="NEW",
        name="",
        chunk_text="",
        url="https://x.example/q",
        metadata={"stock_status": "IN_STOCK"},
    )
    out, relaxed = exclude_already_shown_skus(
        [a, b],
        frozenset({"OLD"}),
        active=True,
    )
    # One unseen item: backfill to reach min 2 → may repeat a shown SKU
    assert out[0].sku == "NEW"
    assert len(out) == 2
    assert relaxed is True
