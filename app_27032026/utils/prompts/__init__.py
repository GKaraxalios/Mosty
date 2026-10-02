"""Modular system prompt assembly for ELIZA / MOSTY."""

from app.utils.prompts.assembly import OutputMode, get_system_instruction
from app.utils.prompts.store_context import (
    CONFIG_PATH,
    build_store_info_el,
    build_store_info_en,
    load_store_content,
)
from app.utils.prompts.system_instruction_content import SYSTEM_RAG_RULES_EL, SYSTEM_RAG_RULES_EN

__all__ = [
    "CONFIG_PATH",
    "OutputMode",
    "SYSTEM_RAG_RULES_EL",
    "SYSTEM_RAG_RULES_EN",
    "build_store_info_el",
    "build_store_info_en",
    "get_system_instruction",
    "load_store_content",
]
