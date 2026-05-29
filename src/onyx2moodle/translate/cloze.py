"""Multi-inlineChoiceInteraction -> Moodle cloze (multianswer) qtype.

ONYX gap-fill shape: the itemBody contains several `<inlineChoiceInteraction>`
elements inline with paragraphs of text. Each has its own response_identifier
and a list of `<inlineChoice>` options; the correct option is named in the
matching `<responseDeclaration><correctResponse><value>`.

Moodle cloze encodes inputs *inline* in the questiontext using markers of the
form `{points:TYPE:=correct#fb~wrong1#fb~wrong2#fb}`. We use TYPE=MULTICHOICE
(vertical dropdown — closest to ONYX's `<inlineChoiceInteraction>`).

Escaping inside option text: the cloze tokeniser is sensitive to the
characters `}`, `~`, `#`, `\\`. Every option string must have those escaped
with a backslash. Math `\\(...\\)` survives because the only backslash that
matters is the one before `}`/`~`/`#`.
"""
from __future__ import annotations

import re

from ..parser import AssessmentItem
from .common import embed_images_as_base64, extract_question_html


def translate(item: AssessmentItem, assets: list = None, category_path: list[str] = None) -> str:
    assets = assets or []
    inline_ixs = [i for i in item.interactions if i.kind == "inlineChoice"]
    if not inline_ixs:
        raise ValueError("cloze translator: no inlineChoiceInteraction")

    # Build a marker per response_identifier and substitute it into the body.
    # We need to *render* each marker after extraction (extract_question_html
    # span-wraps replacements which is fine — cloze markers are plain text).
    markers: dict[str, str] = {}
    for ix in inline_ixs:
        rdecl = item.response_decls.get(ix.response_identifier)
        correct_ids = set(rdecl.correct_values) if rdecl else set()
        if not ix.choices:
            raise ValueError(
                f"cloze translator: inlineChoice {ix.response_identifier} has no options"
            )
        # Order: correct option first (Moodle convention; shuffle handled by
        # the qtype at render time anyway).
        ordered = sorted(
            ix.choices,
            key=lambda c: (0 if c.identifier in correct_ids else 1),
        )
        marker = "{1:MULTICHOICE:" + "~".join(
            ("=" if c.identifier in correct_ids else "") + _escape_option(c.html)
            for c in ordered
        ) + "}"
        markers[ix.response_identifier] = marker

    body_html = extract_question_html(item, markers)
    # extract_question_html wraps replacements in <span>...</span> unless the
    # placeholder matches its `[[...]]` lift regex. Strip the span wrappers
    # around our cloze markers so the `{...}` survives at the top level of
    # the rendered HTML (Moodle cloze parses markers anywhere in the text,
    # but in-span markers can confuse the editor preview).
    body_html = re.sub(r"<span>(\{[^{}]*\})</span>", r"\1", body_html)
    body_html, image_files = embed_images_as_base64(body_html, assets)
    body_with_files = body_html + ("\n" + "\n".join(image_files) if image_files else "")

    return f"""<question type="cloze">
    <name><text>{_escape_attr(item.title or 'Untitled')}</text></name>
    <questiontext format="html">
      <text><![CDATA[{_cdata(body_with_files)}]]></text>
    </questiontext>
    <generalfeedback format="html"><text/></generalfeedback>
    <penalty>0.3333333</penalty>
    <hidden>0</hidden>
    <idnumber/>
  </question>"""


# Cloze option-text escaping. Order matters: backslash first so we don't
# double-escape the slashes we add for the other three.
_CLOZE_SPECIALS = ("\\", "}", "~", "#")


def _escape_option(html: str) -> str:
    s = html
    for ch in _CLOZE_SPECIALS:
        s = s.replace(ch, "\\" + ch)
    return s


def _escape_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cdata(s: str) -> str:
    return s.replace("]]>", "]]]]><![CDATA[>")
