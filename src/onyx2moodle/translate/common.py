"""Shared helpers used by every translator.

Three concerns live here:
  1. Question-body HTML extraction (drop QTI interactions, keep XHTML).
  2. Math delimiter rewriting (ONYX `$$...$$` -> Moodle `\(...\)` / `\[...\]`).
  3. Image embedding for `<questiontext>` `<file>` blocks.
  4. Category path assembly for Moodle category headers.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

from lxml import etree

from ..parser import AssessmentItem, NS, _local, _serialise_children


# ---------------------------------------------------------------------------
# (1) Body HTML extraction
# ---------------------------------------------------------------------------


# Tags that are QTI interactions and must be either dropped or replaced with
# STACK placeholders before the body is shoved into Moodle.
_INTERACTION_TAGS = {
    "textEntryInteraction",
    "choiceInteraction",
    "inlineChoiceInteraction",
    "extendedTextInteraction",
    "uploadInteraction",
    "matchInteraction",
    "hottextInteraction",
    "gapMatchInteraction",
    "graphicGapMatchInteraction",
}


def extract_question_html(
    item: AssessmentItem,
    interaction_replacement: dict[str, str] | None = None,
) -> str:
    """Return the question body as an HTML fragment.

    `interaction_replacement` maps responseIdentifier -> raw HTML/markdown
    to substitute for the QTI interaction element. STACK targets pass
    `{"RESPONSE_1": "[[input:ans1]] [[validation:ans1]]"}`; multichoice
    targets pass `{}` and let the choices be rendered separately.
    """
    interaction_replacement = interaction_replacement or {}
    body_copy = etree.fromstring(etree.tostring(item.body_xml))

    # Walk and replace interaction elements in-place
    for elem in list(body_copy.iter()):
        if _local(elem.tag) not in _INTERACTION_TAGS:
            continue
        rid = elem.get("responseIdentifier") or ""
        repl = interaction_replacement.get(rid, "")
        # Insert as a text node: build a span containing the replacement
        if repl:
            placeholder = etree.SubElement(elem.getparent(), "span")
            placeholder.text = repl
            placeholder.tail = elem.tail
            elem.getparent().replace(elem, placeholder)
        else:
            # Drop the element but preserve its tail text
            parent = elem.getparent()
            idx = list(parent).index(elem)
            if elem.tail:
                if idx == 0:
                    parent.text = (parent.text or "") + elem.tail
                else:
                    prev = parent[idx - 1]
                    prev.tail = (prev.tail or "") + elem.tail
            parent.remove(elem)

    html = _serialise_children(body_copy)
    # Span-wrapper placeholders: lift their content out.
    # `<span>[[input:ans1]] [[validation:ans1]]</span>` -> `[[input:ans1]] [[validation:ans1]]`.
    html = re.sub(
        r"<span>\s*((?:\[\[[^\]]+\]\]\s*)+)</span>",
        r"\1",
        html,
    )
    # ONYX <printedVariable identifier="X"/> -> STACK {@X@} interpolation.
    # The classifier defers items that use this, but rewrite defensively so
    # that any straggler produces valid STACK rather than orphan QTI tags.
    html = re.sub(
        r'<printedVariable\b[^>]*\bidentifier="([^"]+)"[^>]*/>',
        r"{@\1@}",
        html,
    )
    return rewrite_math_delimiters(html.strip())


# ---------------------------------------------------------------------------
# (2) Math delimiters
# ---------------------------------------------------------------------------


_INLINE_DOLLAR_RE = re.compile(r"\$\$(.+?)\$\$", flags=re.DOTALL)


def rewrite_math_delimiters(html: str) -> str:
    """Replace ONYX `$$...$$` (always inline in observed data) with `\\(...\\)`.

    Moodle's MathJax filter recognises `\\(...\\)` for inline and `\\[...\\]`
    for display math. ONYX uses `$$...$$` for *both* inline and display
    (always single-paragraph, no surrounding `\\[`). We default to inline; if
    the LaTeX contains a display-only environment (e.g. `\\begin{align}`),
    we promote to display.
    """
    def repl(m: re.Match[str]) -> str:
        inner = m.group(1)
        if re.search(r"\\begin\{(align|equation|gather|multline|eqnarray)", inner):
            return f"\\[{inner}\\]"
        return f"\\({inner}\\)"
    return _INLINE_DOLLAR_RE.sub(repl, html)


# ---------------------------------------------------------------------------
# (3) Image embedding
# ---------------------------------------------------------------------------


def embed_images_as_base64(html: str, assets: list[Path]) -> tuple[str, list[str]]:
    """Inline `<img src="foo.png">` references using Moodle's `@@PLUGINFILE@@`
    convention plus base64 `<file>` blocks.

    Returns `(rewritten_html, file_blocks)`. `file_blocks` is a list of
    `<file name="..." path="/" encoding="base64">...</file>` strings to be
    placed inside the `<questiontext>` element.
    """
    if not assets:
        return html, []

    by_name = {a.name: a for a in assets}
    used: set[str] = set()

    def src_repl(m: re.Match[str]) -> str:
        original = m.group(1)
        # Match by basename (ONYX paths are bare filenames inside the inner zip)
        name = Path(original).name
        if name in by_name:
            used.add(name)
            return f'src="@@PLUGINFILE@@/{name}"'
        return m.group(0)

    rewritten = re.sub(r'src="([^"]+)"', src_repl, html)

    file_blocks: list[str] = []
    for name in sorted(used):
        data = by_name[name].read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        # Wrap at 76 chars per Moodle's own convention
        wrapped = "\n".join(b64[i:i + 76] for i in range(0, len(b64), 76))
        file_blocks.append(
            f'<file name="{name}" path="/" encoding="base64">{wrapped}</file>'
        )
    return rewritten, file_blocks


# ---------------------------------------------------------------------------
# (4) Category path
# ---------------------------------------------------------------------------


def to_category_path(parts: list[str], root: str = "$course$/top") -> str:
    """Build a Moodle category path like `$course$/top/Algebra/Gruppentheorie`."""
    # Replace underscore-padded ONYX names with real spaces where unambiguous.
    cleaned = [_clean_segment(p) for p in parts if p]
    return "/".join([root, *cleaned]) if cleaned else root


def _clean_segment(seg: str) -> str:
    # ONYX zip directories often have ASCII-safe substitutions ("_" for
    # space, "__" for special chars). The most defensible cleanup is just
    # the double-underscore -> single-underscore collapse; finer-grained
    # un-mangling would corrupt names that legitimately use underscores.
    return seg.replace("__", "_").strip()
