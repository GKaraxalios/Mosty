#!/usr/bin/env python3
"""Print the final ELIZA prompt (system + user) as sent to the model.

Usage (from project root):
  python scripts/print_final_prompt.py [el|en]

Output: SYSTEM block, then USER block. Optional lang: el (default) or en.
"""
from __future__ import annotations

import os
import sys

_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
os.chdir(_project_root)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Optional: load .env so store_content path and any config used by prompt_builder work
_env_file = os.path.join(_project_root, ".env")
if os.path.isfile(_env_file):
    with open(_env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and v:
                    os.environ.setdefault(k, v)

from app.utils.prompt_builder import build_stylist_prompt, get_system_instruction
from app.utils.intent import FashionIntent
from app.models.schemas import RAGSearchResult


def main() -> None:
    lang = (sys.argv[1] if len(sys.argv) > 1 else "el").lower()
    if lang not in ("el", "en"):
        lang = "el"

    # Sample data for user prompt
    query = "θέλω φορέματα για βάφτιση" if lang == "el" else "I want dresses for a baptism"
    intent = FashionIntent(
        intent_type="product_search",
        reference_product=None,
        target_category="Φορέματα",
        search_query="φορέματα βάφτιση",
        style_keywords=["elegant", "refined"],
    )
    results = [
        RAGSearchResult(
            score=0.92,
            sku="DRESS-001",
            name="Μαύρο midi φόρεμα",
            chunk_text="Μαύρο midi φόρεμα με λεπτό ύφασμα, ιδανικό για βραδινό ή ημί-formal περίσταση.",
            url="https://mosty.gr/dress-001",
            metadata={"price": "89.00", "categories": ["Φορέματα", "Βραδινά"]},
        ),
        RAGSearchResult(
            score=0.88,
            sku="DRESS-002",
            name="Νυδ φόρεμα καμπάνα",
            chunk_text="Νυδ φόρεμα καμπάνα, ελαφρύ ύφασμα, για καλοκαιρινές εκδηλώσεις.",
            url="https://mosty.gr/dress-002",
            metadata={"price": "79.00", "categories": ["Φορέματα"]},
        ),
    ]
    conversation_context = (
        "User: ψάχνω κάτι κομψό για γάμο\nAssistant: Σου πρότεινα φορέματα και παπούτσια. "
        "User: μόνο φορέματα και για βάφτιση"
    )
    weather_context = ""

    system_instruction = get_system_instruction(lang, output_mode="json")
    user_prompt = build_stylist_prompt(
        query=query,
        intent=intent,
        results=results,
        lang=lang,
        conversation_context=conversation_context,
        ref_product=None,
        weather_context=weather_context,
    )

    sep = "=" * 72
    print(sep)
    print("SYSTEM (system_instruction)")
    print(sep)
    print(system_instruction)
    print()
    print(sep)
    print("USER (prompt)")
    print(sep)
    print(user_prompt)
    print()
    print(sep)
    print("END FINAL PROMPT")
    print(sep)


if __name__ == "__main__":
    main()
