"""textEntryInteraction (plain string mapping) -> Moodle shortanswer."""
from __future__ import annotations

from ..parser import AssessmentItem
from .common import embed_images_as_base64, extract_question_html


def translate(item: AssessmentItem, assets: list = None, category_path: list[str] = None) -> str:
    assets = assets or []
    text_entries = [i for i in item.interactions if i.kind == "textEntry"]
    if not text_entries:
        raise ValueError("shortanswer translator: no textEntryInteraction")

    # Moodle shortanswer takes one text input per question. If ONYX has more
    # than one, we concatenate the body inline (the first input becomes the
    # answer slot, the rest are dropped — they're typically scaffolding).
    primary = text_entries[0]
    replacements = {primary.response_identifier: "_____"}
    for ix in text_entries[1:]:
        replacements[ix.response_identifier] = "_____"
    body_html = extract_question_html(item, replacements)
    body_html, image_files = embed_images_as_base64(body_html, assets)
    body_with_files = body_html + ("\n" + "\n".join(image_files) if image_files else "")

    rdecl = item.response_decls.get(primary.response_identifier)
    accepted: list[tuple[str, float]] = []
    if rdecl:
        for v in rdecl.correct_values:
            accepted.append((v, 100.0))
        for key, frac in rdecl.mapping.items():
            if not any(key == a[0] for a in accepted):
                accepted.append((key, max(0.0, frac * 100.0)))
    if not accepted:
        accepted = [("", 0.0)]

    answer_blocks = "".join(
        f'    <answer fraction="{frac:.7f}" format="moodle_auto_format">\n'
        f'      <text>{_escape(text)}</text>\n'
        f'      <feedback format="html"><text/></feedback>\n'
        f'    </answer>\n'
        for text, frac in accepted
    )

    return f"""<question type="shortanswer">
    <name><text>{_escape(item.title or 'Untitled')}</text></name>
    <questiontext format="html">
      <text><![CDATA[{_cdata(body_with_files)}]]></text>
    </questiontext>
    <generalfeedback format="html"><text/></generalfeedback>
    <defaultgrade>1.0000000</defaultgrade>
    <penalty>0.3333333</penalty>
    <hidden>0</hidden>
    <idnumber/>
    <usecase>0</usecase>
{answer_blocks}  </question>"""


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cdata(s: str) -> str:
    return s.replace("]]>", "]]]]><![CDATA[>")
