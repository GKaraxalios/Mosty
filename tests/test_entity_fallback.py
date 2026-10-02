"""Regression tests for deterministic entity fallback (colors, category, materials)."""
from app.utils.commerce_semantics import extract_color_lemmas_from_text, extract_material_lemmas_from_text
from app.utils.entity_fallback import (
    apply_entity_fallback,
    extract_occasion_from_query,
    extract_store_category_from_query,
)
from app.utils.intent import FashionIntent


def test_extract_colors_greek_inflections():
    assert "κόκκινο" in extract_color_lemmas_from_text("κόκκινο φόρεμα")
    assert "μαύρο" in extract_color_lemmas_from_text("μαύρα φορέματα")
    assert "λευκό" in extract_color_lemmas_from_text("λευκά sneakers")


def test_extract_material_leather():
    assert "δερμάτινο" in extract_material_lemmas_from_text("δερμάτινα μποτάκια")


def test_category_dress_and_shirt():
    assert extract_store_category_from_query("κόκκινο φόρεμα") == "Φορέματα"
    assert extract_store_category_from_query("μπλε πουκάμισο για δουλειά") == "Πουκάμισα"


def test_category_sneakers_and_boots():
    assert extract_store_category_from_query("λευκά sneakers") == "Παπούτσια"
    assert extract_store_category_from_query("δερμάτινα μποτάκια") == "Μποτάκια"


def test_occasion_work():
    assert extract_occasion_from_query("μπλε πουκάμισο για δουλειά") == "δουλειά"


def test_apply_entity_fallback_fills_empty_intent():
    intent = FashionIntent(intent_type="product_search", search_query="")
    out = apply_entity_fallback("κόκκινο φόρεμα", intent)
    assert out.target_category == "Φορέματα"
    assert "κόκκινο" in out.colors
    assert out.search_query == "κόκκινο φόρεμα"


def test_apply_entity_fallback_does_not_override_llm_colors():
    intent = FashionIntent(
        intent_type="product_search",
        colors=["μπλε"],
        target_category="Φορέματα",
        search_query="test",
    )
    out = apply_entity_fallback("κόκκινο φόρεμα", intent)
    assert out.colors == ["μπλε"]
