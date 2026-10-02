"""Layer 2 — Domain classification & per-class behavior (same block as core in this codebase)."""

from app.utils.prompts.layers.core import _CORE_DOMAIN_AND_BEHAVIOR

DOMAIN_RULES = _CORE_DOMAIN_AND_BEHAVIOR

__all__ = ["DOMAIN_RULES", "_CORE_DOMAIN_AND_BEHAVIOR"]
