"""Tests for soft style novelty detection and lemma expansion."""
from app.models.schemas import ActiveSearchState, RAGSearchResult
from app.services.soft_style_rerank import rerank_chat_retrieval_results
from app.utils.style_refinement import lemmas_for_style_keywords, novelty_requested_from_query


def test_novelty_markers_el():
    assert novelty_requested_from_query("Θέλω κάτι άλλο") is True
    assert novelty_requested_from_query("πιο ιδιαίτερο φόρεμα") is True
    assert novelty_requested_from_query("μαύρο φόρεμα") is False


def test_lemmas_expand_group():
    lem = lemmas_for_style_keywords(["elegant"])
    assert "κομψο" in lem or "elegant" in lem


def test_soft_rerank_boosts_style_match():
    state = ActiveSearchState(style_keywords=["minimal"], last_result_skus=[])
    low = RAGSearchResult(
        score=0.5,
        dense_score=0.0,
        sparse_score=0.0,
        sku="A",
        name="Basic tee",
        chunk_text="cotton shirt",
        url=None,
        metadata={},
    )
    high = RAGSearchResult(
        score=0.45,
        dense_score=0.0,
        sparse_score=0.0,
        sku="B",
        name="Minimal linen dress",
        chunk_text="minimal clean lines simple",
        url=None,
        metadata={},
    )
    out = rerank_chat_retrieval_results(
        [low, high],
        effective_query="φόρεμα",
        merged_state=state,
        user_query="minimal φόρεμα",
    )
    assert out[0].sku == "B"


def test_soft_rerank_novelty_deprioritizes_last_shown():
    state = ActiveSearchState(
        style_keywords=[],
        last_result_skus=["OLD"],
        restrict_to_candidates=False,
    )
    old = RAGSearchResult(
        score=0.9,
        rerank_score=0.9,
        dense_score=0.0,
        sparse_score=0.0,
        sku="OLD",
        name="X",
        chunk_text="y",
        url=None,
        metadata={},
    )
    new = RAGSearchResult(
        score=0.85,
        rerank_score=0.85,
        dense_score=0.0,
        sparse_score=0.0,
        sku="NEW",
        name="Z",
        chunk_text="w",
        url=None,
        metadata={},
    )
    out = rerank_chat_retrieval_results(
        [old, new],
        effective_query="φόρεμα",
        merged_state=state,
        user_query="θέλω κάτι άλλο",
    )
    assert out[0].sku == "NEW"


def test_soft_rerank_llm_refinement_without_phrase():
    state = ActiveSearchState(
        style_keywords=[],
        last_result_skus=["OLD"],
        restrict_to_candidates=False,
    )
    old = RAGSearchResult(
        score=0.9,
        rerank_score=0.9,
        dense_score=0.0,
        sparse_score=0.0,
        sku="OLD",
        name="X",
        chunk_text="y",
        url=None,
        metadata={},
    )
    new = RAGSearchResult(
        score=0.85,
        rerank_score=0.85,
        dense_score=0.0,
        sparse_score=0.0,
        sku="NEW",
        name="Z",
        chunk_text="w",
        url=None,
        metadata={},
    )
    out = rerank_chat_retrieval_results(
        [old, new],
        effective_query="φόρεμα",
        merged_state=state,
        user_query="ok",
        intent_novelty_request=None,
        intent_refinement_request=True,
    )
    assert out[0].sku == "NEW"
