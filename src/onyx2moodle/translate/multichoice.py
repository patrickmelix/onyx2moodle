"""choiceInteraction / inlineChoiceInteraction (single) -> Moodle multichoice."""
from __future__ import annotations

from ..parser import AssessmentItem
from .common import embed_images_as_base64, extract_question_html


def translate(item: AssessmentItem, assets: list = None, category_path: list[str] = None) -> str:
    assets = assets or []
    ix = next(
        (i for i in item.interactions if i.kind in ("choice", "inlineChoice")), None
    )
    if ix is None or not ix.choices:
        raise ValueError("multichoice translator: no choice interaction with options")

    rdecl = item.response_decls.get(ix.response_identifier)
    is_multi = (rdecl and rdecl.cardinality == "multiple") or ix.max_choices == 0
    correct_ids = set(rdecl.correct_values) if rdecl else set()
    mapping = rdecl.mapping if rdecl else {}

    # Body without the interaction itself (choices are emitted separately).
    body_html = extract_question_html(item, {ix.response_identifier: ""})
    body_html, image_files = embed_images_as_base64(body_html, assets)
    body_with_files = body_html + ("\n" + "\n".join(image_files) if image_files else "")

    # Decide per-choice fractions
    n_correct = max(1, sum(1 for c in ix.choices if c.identifier in correct_ids))
    n_wrong = max(1, len(ix.choices) - sum(1 for c in ix.choices if c.identifier in correct_ids))

    answer_blocks: list[str] = []
    for c in ix.choices:
        # Use mapping if present (scaled to 100), else +100/-100 or +100/0 by correctness
        if mapping and c.identifier in mapping:
            raw = mapping[c.identifier]
            # ONYX mapping values are typically -1.0 / 0 / +1.0 per option.
            # Moodle expects fractions in -100..100 that *sum to 100 across the
            # correct picks*; we scale by counts.
            if raw > 0:
                fraction = 100.0 / n_correct
            elif raw < 0:
                fraction = -100.0 / n_wrong
            else:
                fraction = 0.0
        else:
            if c.identifier in correct_ids:
                fraction = 100.0 / n_correct
            else:
                fraction = (-100.0 / n_wrong) if is_multi else 0.0

        answer_blocks.append(_render_answer(c.html, fraction))

    single_attr = "false" if is_multi else "true"
    return f"""<question type="multichoice">
    <name><text>{_escape(item.title or 'Untitled')}</text></name>
    <questiontext format="html">
      <text><![CDATA[{_cdata(body_with_files)}]]></text>
    </questiontext>
    <generalfeedback format="html"><text/></generalfeedback>
    <defaultgrade>1.0000000</defaultgrade>
    <penalty>0.3333333</penalty>
    <hidden>0</hidden>
    <idnumber/>
    <single>{single_attr}</single>
    <shuffleanswers>{'true' if ix.shuffle else 'false'}</shuffleanswers>
    <answernumbering>abc</answernumbering>
    <showstandardinstruction>0</showstandardinstruction>
    <correctfeedback format="html"><text><![CDATA[<p>Richtig.</p>]]></text></correctfeedback>
    <partiallycorrectfeedback format="html"><text><![CDATA[<p>Teilweise richtig.</p>]]></text></partiallycorrectfeedback>
    <incorrectfeedback format="html"><text><![CDATA[<p>Noch nicht richtig.</p>]]></text></incorrectfeedback>
{''.join(answer_blocks)}  </question>"""


def _render_answer(html: str, fraction: float) -> str:
    return (
        f'    <answer fraction="{fraction:.7f}" format="html">\n'
        f'      <text><![CDATA[{_cdata(html)}]]></text>\n'
        f'      <feedback format="html"><text/></feedback>\n'
        f'    </answer>\n'
    )


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cdata(s: str) -> str:
    return s.replace("]]>", "]]]]><![CDATA[>")
