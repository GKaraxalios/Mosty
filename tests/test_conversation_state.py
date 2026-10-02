"""Tests for ActiveSearchState merge, price parsing, candidate filtering."""
from app.models.schemas import ActiveSearchState
from app.utils.conversation_state import (
    detect_restrict_to_candidates,
    filter_results_by_candidate_skus,
    merge_active_search_state,
    parse_price_range_el,
)
from app.utils.intent import FashionIntent


def test_parse_price_range():
    assert parse_price_range_el("από 90 έως 120 ευρώ") == (90.0, 120.0)
    assert parse_price_range_el("90-120") == (90.0, 120.0)
    assert parse_price_range_el("κάτω από 100") == (None, 100.0)
    assert parse_price_range_el("μέχρι 80 ευρώ") == (None, 80.0)


def test_detect_restrict():
    assert detect_restrict_to_candidates("Από τις επιλογές θέλω φθηνότερα") is True
    assert detect_restrict_to_candidates("Θέλω κόκκινο φόρεμα") is False


def test_merge_first_turn():
    intent = FashionIntent(
        intent_type="product_search",
        target_category="Φορέματα",
        colors=["μαύρο"],
        search_query="μαύρα φορέματα",
    )
    st = merge_active_search_state(None, intent, "μαύρα φορέματα", False, [])
    assert st.category == "Φορέματα"
    assert st.color == "μαύρο"


def test_merge_accumulates_style_keywords():
    prev = ActiveSearchState(
        category="Φορέματα",
        style_keywords=["elegant"],
        style="elegant",
    )
    intent = FashionIntent(
        intent_type="product_search",
        search_query="πιο minimal",
        style_keywords=["minimal"],
    )
    st = merge_active_search_state(prev, intent, "πιο minimal", True, [])
    assert st.style_keywords == ["elegant", "minimal"]
    assert st.style == "minimal"


def test_merge_follow_up_price():
    prev = ActiveSearchState(category="Φορέματα", color="μαύρο")
    intent = FashionIntent(intent_type="product_search", search_query="τιμή")
    st = merge_active_search_state(
        prev,
        intent,
        "Από τις επιλογές από 90 έως 120 ευρώ",
        True,
        ["SKU1", "SKU2"],
    )
    assert st.min_price == 90.0 and st.max_price == 120.0
    assert st.category == "Φορέματα"
    assert st.restrict_to_candidates is True
    assert st.candidate_skus == ["SKU1", "SKU2"]


def test_candidate_filter_keeps_subset():
    from app.models.schemas import RAGSearchResult

    st = ActiveSearchState(
        restrict_to_candidates=True,
        candidate_skus=["A", "B"],
    )
    results = [
        RAGSearchResult(
            score=1.0,
            dense_score=0.0,
            sparse_score=0.0,
            sku="A",
            name="",
            chunk_text="",
            url="https://x",
            metadata={"stock_status": "IN_STOCK"},
        ),
        RAGSearchResult(
            score=0.9,
            dense_score=0.0,
            sparse_score=0.0,
            sku="C",
            name="",
            chunk_text="",
            url="https://x",
            metadata={"stock_status": "IN_STOCK"},
        ),
    ]
    out = filter_results_by_candidate_skus(results, st)
    assert len(out) == 1 and out[0].sku == "A"
