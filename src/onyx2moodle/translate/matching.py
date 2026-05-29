"""matchInteraction -> Moodle matching qtype.

ONYX `matchInteraction` shape:
    <responseDeclaration baseType="directedPair" cardinality="multiple">
      <correctResponse>
        <value>sourceId targetId</value>
        ...
      </correctResponse>
    </responseDeclaration>
    <matchInteraction>
      <simpleMatchSet>  <!-- sources / "stems" -->
        <simpleAssociableChoice identifier="...">HTML</simpleAssociableChoice>
      </simpleMatchSet>
      <simpleMatchSet>  <!-- targets / "answers" -->
        <simpleAssociableChoice identifier="...">HTML</simpleAssociableChoice>
      </simpleMatchSet>
    </matchInteraction>

Moodle `matching` shape:
    <subquestion><text>stem</text><answer><text>answer</text></answer></subquestion>
    <subquestion><text/><answer><text>distractor</text></answer></subquestion>

A `subquestion` with empty stem is the Moodle idiom for a distractor answer
(target choice that doesn't pair with any stem). Distractors arise naturally
when ONYX's target set has more options than the source set has paired stems.
"""
from __future__ import annotations

from ..parser import AssessmentItem
from .common import embed_images_as_base64, extract_question_html


def translate(item: AssessmentItem, assets: list = None, category_path: list[str] = None) -> str:
    assets = assets or []
    ix = next((i for i in item.interactions if i.kind == "match"), None)
    if ix is None:
        raise ValueError("matching translator: no matchInteraction")
    if not ix.match_sources or not ix.match_targets:
        raise ValueError("matching translator: empty source or target set")

    rdecl = item.response_decls.get(ix.response_identifier)
    if rdecl is None or not rdecl.correct_values:
        raise ValueError("matching translator: no correctResponse pairs")

    # Build source_id -> target_id from "src tgt" pair strings.
    # ONYX cardinality="multiple" with directedPair allows a source to have
    # multiple correct targets in principle, but Moodle matching is 1:1 — if a
    # source repeats we keep the first pair and surface the rest as separate
    # subquestions with the same stem (Moodle accepts that).
    pairs: list[tuple[str, str]] = []
    for v in rdecl.correct_values:
        parts = v.strip().split()
        if len(parts) == 2:
            pairs.append((parts[0], parts[1]))

    src_by_id = {c.identifier: c.html for c in ix.match_sources}
    tgt_by_id = {c.identifier: c.html for c in ix.match_targets}

    body_html = extract_question_html(item, {ix.response_identifier: ""})
    body_html, image_files = embed_images_as_base64(body_html, assets)
    body_with_files = body_html + ("\n" + "\n".join(image_files) if image_files else "")

    subquestions: list[str] = []
    paired_target_ids: set[str] = set()
    for sid, tid in pairs:
        stem_html = src_by_id.get(sid)
        answer_html = tgt_by_id.get(tid)
        if stem_html is None or answer_html is None:
            # Skip pairs that reference unknown ids — better to drop than to
            # silently emit a half-broken subquestion.
            continue
        paired_target_ids.add(tid)
        subquestions.append(_render_subquestion(stem_html, answer_html))

    # Distractors: target choices that never appear in a correct pair.
    for c in ix.match_targets:
        if c.identifier not in paired_target_ids:
            subquestions.append(_render_subquestion("", c.html))

    if len(subquestions) < 2:
        # Moodle's matching requires at least 2 subquestions; otherwise it
        # silently fails to import. Surface this rather than emit garbage.
        raise ValueError("matching translator: need at least 2 subquestions after pairing")

    return f"""<question type="matching">
    <name><text>{_escape(item.title or 'Untitled')}</text></name>
    <questiontext format="html">
      <text><![CDATA[{_cdata(body_with_files)}]]></text>
    </questiontext>
    <generalfeedback format="html"><text/></generalfeedback>
    <defaultgrade>1.0000000</defaultgrade>
    <penalty>0.3333333</penalty>
    <hidden>0</hidden>
    <idnumber/>
    <shuffleanswers>{'true' if ix.shuffle else 'false'}</shuffleanswers>
    <correctfeedback format="html"><text><![CDATA[<p>Richtig.</p>]]></text></correctfeedback>
    <partiallycorrectfeedback format="html"><text><![CDATA[<p>Teilweise richtig.</p>]]></text></partiallycorrectfeedback>
    <incorrectfeedback format="html"><text><![CDATA[<p>Noch nicht richtig.</p>]]></text></incorrectfeedback>
    <shownumcorrect/>
{''.join(subquestions)}  </question>"""


def _render_subquestion(stem_html: str, answer_html: str) -> str:
    return (
        '    <subquestion format="html">\n'
        f'      <text><![CDATA[{_cdata(stem_html)}]]></text>\n'
        '      <answer format="html">\n'
        f'        <text><![CDATA[{_cdata(answer_html)}]]></text>\n'
        '      </answer>\n'
        '    </subquestion>\n'
    )


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cdata(s: str) -> str:
    return s.replace("]]>", "]]]]><![CDATA[>")
