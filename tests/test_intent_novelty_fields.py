"""Intent JSON: novelty_request / refinement_request tri-state parsing."""
import json

from app.utils.intent import FashionIntent, parse_intent


def test_parse_explicit_booleans():
    raw = json.dumps(
        {
            "intent_type": "product_search",
            "search_query": "φορέματα",
            "novelty_request": True,
            "refinement_request": False,
        }
    )
    i = parse_intent(raw, "φορέματα")
    assert i.novelty_request is True
    assert i.refinement_request is False


def test_parse_missing_keys_none():
    raw = json.dumps({"intent_type": "product_search", "search_query": "x"})
    i = parse_intent(raw, "x")
    assert i.novelty_request is None
    assert i.refinement_request is None


def test_fallback_intent_defaults():
    i = FashionIntent(search_query="q")
    assert i.novelty_request is None
    assert i.refinement_request is None
