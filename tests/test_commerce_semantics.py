"""Tests for Greek commerce normalization and hybrid filter helpers."""
from app.models.schemas import RAGSearchResult
from app.utils.commerce_semantics import (
    category_matches_filter,
    enrich_search_query,
    extract_color_lemmas_from_text,
    fold_el,
    haystack_matches_color,
    prune_by_excluded_keywords,
)


def test_fold_el_strips_tonos():
    assert fold_el("Μαύρο") == fold_el("ΜΑΥΡΟ")
    assert "μαυρο" == fold_el("μαύρο")


def test_color_plural_matches_singular_in_haystack():
    hay = "evening dress black satin"
    assert haystack_matches_color(hay, "μαύρα") is True
    assert haystack_matches_color(hay, "λευκό") is False


def test_category_partial_overlap():
    assert category_matches_filter("Φορέματα", ["Women", "Dresses"]) is False
    assert category_matches_filter("Φορέματα", ["Φορέματα", "Mini"]) is True


def test_enrich_search_query_appends_missing_tokens():
    q = enrich_search_query(
        "φόρεμα",
        {
            "colors": ["μαύρο"],
            "materials": [],
            "occasion": None,
            "season": None,
            "fit": None,
            "target_category": "Φορέματα",
            "style_keywords": ["elegant"],
        },
    )
    assert "μαύρο" in q
    assert "Φορέματα" in q
    assert "elegant" in q


def test_extract_color_lemmas_presence():
    lemmas = extract_color_lemmas_from_text("κόκκινο και μπλε ριγέ")
    assert "κόκκινο" in lemmas
    assert "μπλε" in lemmas


def test_prune_by_excluded_keywords():
    results = [
        RAGSearchResult(
            score=1.0,
            dense_score=0.0,
            sparse_score=0.0,
            sku="a",
            name="Floral dress",
            chunk_text="cotton summer",
            url="",
            metadata={"categories": []},
        ),
        RAGSearchResult(
            score=0.9,
            dense_score=0.0,
            sparse_score=0.0,
            sku="b",
            name="Black midi",
            chunk_text="viscose",
            url="",
            metadata={"categories": []},
        ),
    ]
    out = prune_by_excluded_keywords(results, ["floral"])
    assert len(out) == 1
    assert out[0].sku == "b"
