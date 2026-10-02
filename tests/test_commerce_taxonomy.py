"""Commerce color taxonomy + intent merge (regression)."""
from app.models.schemas import HybridSearchFilters
from app.utils.color_taxonomy import chunk_matches_color, resolve_color_canonical
from app.utils.commerce_retrieval import (
    apply_session_color_carryover,
    filter_results_by_target_category,
    finalize_commerce_retrieval,
    merge_intent_into_filters,
    post_filter_by_color,
    post_filter_by_price,
    relaxed_filters_drop_color_only,
    relaxed_filters_drop_soft_attributes,
)
from app.utils.color_taxonomy import (
    extract_color_from_query_text,
    normalize_greek_commerce_query,
)
from app.utils.occasion_rules import occasion_style_keywords
from app.utils.intent import FashionIntent, _ensure_default_category_strict
from app.models.schemas import RAGSearchResult
from app.services.hybrid_search_service import _category_filter_matches_chunk


def test_resolve_greek_colors():
    assert resolve_color_canonical("κόκκινο") == "red"
    assert resolve_color_canonical("λευκό") == "white"
    assert resolve_color_canonical("μπλε") == "blue"


def test_chunk_matches_not_pink_as_white():
    hay = "φόρεμα ροζ cotton"
    assert chunk_matches_color(hay, "white") is False
    assert chunk_matches_color(hay, "pink") is True


def test_merge_intent_sets_color_canonical():
    fi = FashionIntent(
        search_query="φόρεμα",
        color="red",
        occasion_key="wedding",
    )
    f = merge_intent_into_filters(HybridSearchFilters(), fi)
    assert f.color_canonical == "red"
    assert f.occasion is not None


def test_evening_occasion_ranking_hints_exclude_glam_satin():
    """Occasion must not inject fabric/glam as pseudo-hard constraints."""
    kws = occasion_style_keywords("evening_event")
    assert "glam" not in kws and "σατέν" not in kws
    assert "βραδινό" in kws or "evening" in kws


def test_evening_event_does_not_set_occasion_metadata_filter():
    """βραδινή έξοδο maps to evening_event; hard substring 'βραδιν' was dropping all dresses."""
    fi = FashionIntent(
        search_query="φόρεμα βραδινή έξοδο",
        target_category="Φορέματα",
        occasion_key="evening_event",
    )
    f = merge_intent_into_filters(HybridSearchFilters(), fi)
    assert f.occasion is None
    assert f.category == "Φορέματα"


def test_category_filter_matches_greek_intent_to_english_paths():
    """Store chunks often use English category paths while intent uses Greek labels."""
    cat = "φορέματα"
    hay = "women dresses evening"
    assert _category_filter_matches_chunk(cat, ["Women", "Dresses"], hay) is True


def test_category_filter_matches_via_product_name_when_categories_empty():
    assert _category_filter_matches_chunk(
        "φορέματα",
        [],
        "stunning black dress — sizes s-xl",
    ) is True


def test_category_filter_rejects_unrelated_product():
    assert _category_filter_matches_chunk(
        "φορέματα",
        ["Jeans"],
        "skinny jeans blue",
    ) is False


def test_post_filter_drops_wrong_color():
    r_ok = RAGSearchResult(
        score=1.0,
        sku="A",
        name="Φόρεμα κόκκινο",
        chunk_text="κόκκινο",
        metadata={"categories": ["Φορέματα"]},
    )
    r_bad = RAGSearchResult(
        score=1.0,
        sku="B",
        name="Φόρεμα μπλε",
        chunk_text="μπλε σατέν",
        metadata={"categories": ["Φορέματα"]},
    )
    out = post_filter_by_color([r_ok, r_bad], "red", color_strict=True)
    assert len(out) == 1
    assert out[0].sku == "A"


def test_relaxed_filters_drop_soft_attributes_keeps_category_and_price():
    f = HybridSearchFilters(
        category="Φορέματα",
        color_canonical="red",
        size="M",
        min_price=20.0,
        max_price=100.0,
        occasion="βραδινό",
    )
    r = relaxed_filters_drop_soft_attributes(f)
    assert r.category == "Φορέματα"
    assert r.min_price == 20.0 and r.max_price == 100.0
    assert r.color_canonical is None and r.size is None and r.occasion is None


def test_post_filter_by_price_drops_out_of_range():
    r_in = RAGSearchResult(
        score=1.0,
        sku="A",
        name="Item A",
        chunk_text="x",
        metadata={"price": 45.0},
    )
    r_high = RAGSearchResult(
        score=1.0,
        sku="B",
        name="Item B",
        chunk_text="x",
        metadata={"price": 120.0},
    )
    r_unknown = RAGSearchResult(
        score=1.0,
        sku="C",
        name="Item C",
        chunk_text="x",
        metadata={},
    )
    out = post_filter_by_price([r_in, r_high, r_unknown], min_price=40.0, max_price=50.0)
    skus = {r.sku for r in out}
    assert skus == {"A", "C"}


def test_normalize_greek_typo_mavra():
    assert "μαύρα" in normalize_greek_commerce_query("μάυρα φορέματα")


def test_extract_color_typo_mavra_query():
    assert extract_color_from_query_text("μάυρα φορέματα") == "black"


def test_finalize_keeps_only_matching_colors_when_possible():
    r_black = RAGSearchResult(
        score=1.0,
        sku="K",
        name="Φόρεμα μαύρο",
        chunk_text="μαύρο βαμβακερό",
        metadata={"categories": ["Φορέματα"]},
    )
    r_white = RAGSearchResult(
        score=0.9,
        sku="W",
        name="Φόρεμα λευκό",
        chunk_text="λευκό",
        metadata={"categories": ["Φορέματα"]},
    )
    intent = FashionIntent(
        search_query="μαύρα φορέματα",
        target_category="Φορέματα",
        color_canonical="black",
    )
    out = finalize_commerce_retrieval(intent, "μαύρα φορέματα", [r_black, r_white])
    assert out.match_tier == "exact"
    assert len(out.results) == 1
    assert out.results[0].sku == "K"


def test_finalize_alternatives_when_no_color_match():
    r_white = RAGSearchResult(
        score=1.0,
        sku="W",
        name="Φόρεμα λευκό",
        chunk_text="λευκό",
        metadata={"categories": ["Φορέματα"]},
    )
    intent = FashionIntent(
        search_query="μαύρα φορέματα",
        target_category="Φορέματα",
        color_canonical="black",
    )
    out = finalize_commerce_retrieval(intent, "μαύρα φορέματα", [r_white])
    assert out.match_tier == "alternatives"
    assert len(out.results) == 1


def test_relaxed_filters_drop_color_only_keeps_category():
    f = HybridSearchFilters(
        category="Φορέματα",
        color_canonical="brown",
        color="καφέ",
    )
    r = relaxed_filters_drop_color_only(f)
    assert r.category == "Φορέματα"
    assert r.color_canonical is None
    assert r.color is None


def test_apply_session_color_carryover_from_last_query():
    """Short follow-up without color still inherits canonical color from previous turn."""
    intent = FashionIntent(
        search_query="άλλα",
        target_category="Φορέματα",
        color_canonical=None,
    )
    prev = "Θέλω καφέ φορέματα"
    out = apply_session_color_carryover(
        intent,
        last_user_query=prev,
        current_query="δεν έχει άλλα το site;",
    )
    assert out.color_canonical == "brown"


def test_ensure_default_category_strict_adds_category_constraint():
    i = FashionIntent(
        intent_type="product_search",
        target_category="Φορέματα",
        search_query="φόρεμα βραδινό",
        strict_constraints=[],
    )
    o = _ensure_default_category_strict(i)
    assert "category" in o.strict_constraints


def test_filter_results_by_target_category_drops_jewelry_for_dresses():
    dress = RAGSearchResult(
        score=0.9,
        sku="D1",
        name="Midi φόρεμα",
        chunk_text="φόρεμα βραδινό",
        metadata={"categories": ["Φορέματα"]},
    )
    necklace = RAGSearchResult(
        score=0.95,
        sku="J1",
        name="Κολιέ αλυσίδα",
        chunk_text="κολιέ χρυσό",
        metadata={"categories": ["Κολιέ", "Κοσμήματα"]},
    )
    out = filter_results_by_target_category([dress, necklace], "Φορέματα")
    assert len(out) == 1
    assert out[0].sku == "D1"
