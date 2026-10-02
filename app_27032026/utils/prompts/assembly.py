"""Assemble the full system instruction — single unified document (see `unified_system_instruction`)."""
from __future__ import annotations

from app.utils.prompts.unified_system_instruction import (
    OutputMode,
    build_unified_system_instruction,
)

__all__ = ["OutputMode", "get_system_instruction"]


def get_system_instruction(lang: str, output_mode: OutputMode = "text") -> str:
    """One strong system prompt: preamble + domain + role + stylist + store + output + RAG rules."""
    return build_unified_system_instruction(lang, output_mode)
