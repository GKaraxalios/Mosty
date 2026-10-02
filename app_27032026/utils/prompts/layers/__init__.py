"""Logical layer re-exports (same strings as `system_instruction_content`; use for navigation / future split)."""

from app.utils.prompts.system_instruction_content import (
    _CORE_DOMAIN_AND_BEHAVIOR,
    _ELIZA_ROLE_AND_STYLING_FIRST,
    _ELIZA_STYLIST_AND_FORMAT,
    _OUTPUT_FORMAT_JSON,
    _OUTPUT_FORMAT_TEXT,
    SYSTEM_RAG_RULES_EL,
    SYSTEM_RAG_RULES_EN,
)

__all__ = [
    "SYSTEM_RAG_RULES_EL",
    "SYSTEM_RAG_RULES_EN",
    "_CORE_DOMAIN_AND_BEHAVIOR",
    "_ELIZA_ROLE_AND_STYLING_FIRST",
    "_ELIZA_STYLIST_AND_FORMAT",
    "_OUTPUT_FORMAT_JSON",
    "_OUTPUT_FORMAT_TEXT",
]
