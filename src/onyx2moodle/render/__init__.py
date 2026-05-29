"""Template-based XML rendering helpers (slot substitution + XML escaping)."""

from .templates import (
    load_template,
    substitute_cdata,
    substitute_escaped,
    substitute_raw,
)

__all__ = [
    "load_template",
    "substitute_cdata",
    "substitute_escaped",
    "substitute_raw",
]
