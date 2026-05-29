"""Slot substitution for templated XML emission.

We use `%%KEY%%` placeholders rather than `{key}` or `$key` because:
  - `%` is a Maxima sigil (`%pi`, `%e`, `%i`) and `{...}` is Maxima set
    notation; both occur literally inside STACK question variables.
  - `{@var@}` is the Moodle interpolation syntax used in `<questionnote>`.

The double-`%` delimiter avoids collisions with all three.

Two substitution flavours:
  - `substitute_escaped`: XML-escape `& < >` before inserting (use for tag content).
  - `substitute_cdata`:   pre-escape `]]>` only (use inside `<![CDATA[ ... ]]>`).
  - `substitute_raw`:     no escaping (use for already-rendered XML fragments).
"""
from __future__ import annotations

from pathlib import Path

# Templates ship alongside the package; this resolves to .../onyx2moodle/templates/
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def load_template(name: str) -> str:
    """Load a bundled template by basename (e.g. 'question_shell.xml')."""
    p = _TEMPLATES_DIR / name
    if not p.exists():
        raise FileNotFoundError(f"Template not found: {p}")
    return p.read_text(encoding="utf-8")


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
    )


def _cdata_escape(value: str) -> str:
    # Avoid stranding a "]]>" inside a CDATA section.
    return value.replace("]]>", "]]]]><![CDATA[>")


def substitute_escaped(template: str, slot: str, value: str) -> str:
    return template.replace(f"%%{slot}%%", _xml_escape(value))


def substitute_cdata(template: str, slot: str, value: str) -> str:
    return template.replace(f"%%{slot}%%", _cdata_escape(value))


def substitute_raw(template: str, slot: str, value: str) -> str:
    return template.replace(f"%%{slot}%%", value)
