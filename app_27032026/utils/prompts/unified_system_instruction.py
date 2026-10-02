"""Single composed system instruction: one document from all policy layers.

Layers remain defined in `system_instruction_content.py`; this module joins them with
a master preamble so the model treats everything as one coherent policy.
"""
from __future__ import annotations

from typing import Literal

from app.utils.prompts.store_context import build_store_info_el, build_store_info_en
from app.utils.prompts.system_instruction_content import (
    _CORE_DOMAIN_AND_BEHAVIOR_EL,
    _CORE_DOMAIN_AND_BEHAVIOR_EN,
    _ELIZA_ROLE_AND_STYLING_FIRST_EL,
    _ELIZA_ROLE_AND_STYLING_FIRST_EN,
    _ELIZA_STYLIST_AND_FORMAT_EL,
    _ELIZA_STYLIST_AND_FORMAT_EN,
    _OUTPUT_FORMAT_JSON_EL,
    _OUTPUT_FORMAT_JSON_EN,
    _OUTPUT_FORMAT_TEXT_EL,
    _OUTPUT_FORMAT_TEXT_EN,
    SYSTEM_RAG_RULES_EL,
    SYSTEM_RAG_RULES_EN,
)

OutputMode = Literal["text", "json"]

_UNIFIED_PREAMBLE_EL = """
████████████████████████████████████████████████████████████████████████████████
ELIZA AI AGENT — MOSTY | ΕΝΙΑΙΟ ΣΥΣΤΗΜΑ ΟΔΗΓΙΩΝ (ένα έγγραφο, μία πολιτική)
████████████████████████████████████████████████████████████████████████████████

Όλες οι ενότητες παρακάτω (DOMAIN → ROLE → STYLIST → ΚΑΤΑΣΤΗΜΑ → ΜΟΡΦΗ ΕΞΟΔΟΥ → RAG)
ισχύουν ΜΑΖΙ. Διάβασέ τες ως ενιαίο κανονιστικό πλαίσιο, όχι ως ξεχωριστές «ταυτότητες».

ΠΡΟΤΕΡΑΙΟΤΗΤΑ όταν κάτι φαίνεται αντιφατικό:
  1) Αλήθεια καταλόγου — ποτέ εφεύρεση SKU, τιμών, URL, διαθεσιμότητας
  2) Styling & χρησιμότητα πριν από «αποτυχία αναζήτησης» — βοήθα σαν stylist
  3) Γλώσσα & τόνος χρήστη — premium, φυσικός, σύντομος
  4) HARD constraints (budget, stock, ρητές απαιτήσεις) πάντα πάνω από SOFT προτιμήσεις

Στόχος: ένας συνεπής shopping stylist για το MOSTY, όχι γενικό chatbot και όχι μηχάνημα «δεν βρέθηκε».

████████████████████████████████████████████████████████████████████████████████

""".strip() + "\n\n"

_UNIFIED_PREAMBLE_EN = """
████████████████████████████████████████████████████████████████████████████████
ELIZA AI AGENT — MOSTY | UNIFIED SYSTEM INSTRUCTION (one document, one policy)
████████████████████████████████████████████████████████████████████████████████

All sections below (DOMAIN → ROLE → STYLIST → STORE → OUTPUT FORMAT → RAP/RAG RULES)
apply TOGETHER. Read them as a single regulatory frame, not separate “identities.”

Priority when anything seems contradictory:
  1) Catalog truth — never invent SKU, price, URL, or availability
  2) Styling value before “search failed” phrasing — behave as a stylist
  3) User language & tone — premium, natural, concise
  4) HARD constraints (budget, stock, explicit asks) always override SOFT preferences

Goal: one consistent MOSTY shopping stylist, not a generic chatbot or a “nothing found” engine.

████████████████████████████████████████████████████████████████████████████████

""".strip() + "\n\n"


def build_unified_system_instruction(lang: str, output_mode: OutputMode = "text") -> str:
    """Return the full system instruction as one concatenated document (EL or EN)."""
    is_el = lang == "el"
    preamble = _UNIFIED_PREAMBLE_EL if is_el else _UNIFIED_PREAMBLE_EN
    core = _CORE_DOMAIN_AND_BEHAVIOR_EL if is_el else _CORE_DOMAIN_AND_BEHAVIOR_EN
    role = _ELIZA_ROLE_AND_STYLING_FIRST_EL if is_el else _ELIZA_ROLE_AND_STYLING_FIRST_EN
    stylist = _ELIZA_STYLIST_AND_FORMAT_EL if is_el else _ELIZA_STYLIST_AND_FORMAT_EN
    store = build_store_info_el() if is_el else build_store_info_en()
    if output_mode == "json":
        output_block = _OUTPUT_FORMAT_JSON_EL if is_el else _OUTPUT_FORMAT_JSON_EN
    else:
        output_block = _OUTPUT_FORMAT_TEXT_EL if is_el else _OUTPUT_FORMAT_TEXT_EN
    rules = SYSTEM_RAG_RULES_EL if is_el else SYSTEM_RAG_RULES_EN

    sep = "\n\n" + "═" * 72 + "\n\n"
    parts = [
        preamble,
        "【 DOMAIN & BEHAVIOR 】\n\n" if not is_el else "【 ΠΕΔΙΟ & ΣΥΜΠΕΡΙΦΟΡΑ 】\n\n",
        core,
        sep,
        "【 ROLE — STYLING FIRST 】\n\n" if not is_el else "【 ΡΟΛΟΣ — ΠΡΩΤΑ STYLING 】\n\n",
        role,
        sep,
        "【 STYLIST INTELLIGENCE 】\n\n" if not is_el else "【 STYLIST — ΛΕΠΤΟΜΕΡΕΙΕΣ 】\n\n",
        stylist,
        sep,
        "【 STORE CONTEXT (JSON / FACTS) 】\n\n" if not is_el else "【 ΚΑΤΑΣΤΗΜΑ (JSON / ΓΕΓΟΝΟΤΑ) 】\n\n",
        store,
        sep,
        "【 OUTPUT CONTRACT 】\n\n" if not is_el else "【 ΜΟΡΦΗ ΕΞΟΔΟΥ 】\n\n",
        output_block,
        sep,
        "【 RAG — PRODUCTS, TRUST, MEMORY 】\n\n" if not is_el else "【 RAG — ΠΡΟΪΟΝΤΑ, ΕΜΠΙΣΤΟΣΥΝΗ, ΜΝΗΜΗ 】\n\n",
        rules,
    ]
    return "".join(parts)
