"""End-to-end tests for the matching and cloze translators.

Each test parses a synthetic ONYX item, runs the translator, wraps the
output in a `<quiz>` envelope and re-parses it with lxml — the strictest
well-formedness check we can do without booting Moodle. The translators'
target XML shapes are then asserted structurally.
"""
from __future__ import annotations

from pathlib import Path

from lxml import etree

from onyx2moodle.classifier import classify
from onyx2moodle.parser import parse_item
from onyx2moodle.translate import cloze, matching

MATCH_ITEM = """<?xml version="1.0" encoding="UTF-8"?>
<assessmentItem xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1"
                identifier="ix" title="Test Match" adaptive="false" timeDependent="false">
  <responseDeclaration identifier="RESPONSE_1" cardinality="multiple" baseType="directedPair">
    <correctResponse>
      <value>SRC_A TGT_1</value>
      <value>SRC_B TGT_2</value>
    </correctResponse>
  </responseDeclaration>
  <itemBody>
    <p>Match the items.</p>
    <matchInteraction responseIdentifier="RESPONSE_1" shuffle="true" maxAssociations="0">
      <simpleMatchSet>
        <simpleAssociableChoice identifier="SRC_A" matchMax="1"><p>Stem A</p></simpleAssociableChoice>
        <simpleAssociableChoice identifier="SRC_B" matchMax="1"><p>Stem B</p></simpleAssociableChoice>
      </simpleMatchSet>
      <simpleMatchSet>
        <simpleAssociableChoice identifier="TGT_1" matchMax="1"><p>Answer 1</p></simpleAssociableChoice>
        <simpleAssociableChoice identifier="TGT_2" matchMax="1"><p>Answer 2</p></simpleAssociableChoice>
        <simpleAssociableChoice identifier="TGT_3" matchMax="1"><p>Distractor</p></simpleAssociableChoice>
      </simpleMatchSet>
    </matchInteraction>
  </itemBody>
</assessmentItem>
"""


CLOZE_ITEM = """<?xml version="1.0" encoding="UTF-8"?>
<assessmentItem xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1"
                identifier="ix" title="Test Cloze" adaptive="false" timeDependent="false">
  <responseDeclaration identifier="RESPONSE_1" cardinality="single" baseType="identifier">
    <correctResponse><value>ID_1</value></correctResponse>
  </responseDeclaration>
  <responseDeclaration identifier="RESPONSE_2" cardinality="single" baseType="identifier">
    <correctResponse><value>ID_3</value></correctResponse>
  </responseDeclaration>
  <itemBody>
    <p>Das neutrale Element ist <inlineChoiceInteraction responseIdentifier="RESPONSE_1" shuffle="false">
      <inlineChoice identifier="ID_1">$$f_1$$</inlineChoice>
      <inlineChoice identifier="ID_2">$$f_2$$</inlineChoice>
    </inlineChoiceInteraction>
    und das inverse zu $$f_4$$ ist <inlineChoiceInteraction responseIdentifier="RESPONSE_2" shuffle="false">
      <inlineChoice identifier="ID_1">$$f_1$$</inlineChoice>
      <inlineChoice identifier="ID_3">$$f_3$$</inlineChoice>
    </inlineChoiceInteraction>.</p>
  </itemBody>
</assessmentItem>
"""


def _wrap_and_parse(question_block: str) -> etree._Element:
    wrapped = f'<?xml version="1.0" encoding="UTF-8"?>\n<quiz>\n{question_block}\n</quiz>\n'
    return etree.fromstring(wrapped.encode())


def test_matching_translation_well_formed(tmp_path: Path) -> None:
    p = tmp_path / "m.xml"
    p.write_text(MATCH_ITEM)
    a = parse_item(p)
    assert classify(a).target == "matching"
    out = matching.translate(a, assets=[], category_path=["Algebra"])
    root = _wrap_and_parse(out)
    q = root.find("question")
    assert q.get("type") == "matching"

    subs = q.findall("subquestion")
    # 2 real pairs + 1 distractor
    assert len(subs) == 3

    stems = [s.findtext("text") or "" for s in subs]
    answers = [s.findtext("answer/text") or "" for s in subs]
    assert any("Stem A" in s for s in stems)
    assert any("Stem B" in s for s in stems)
    assert any("Answer 1" in a for a in answers)
    assert any("Answer 2" in a for a in answers)
    # Distractor: stem empty, answer present
    distractor_pos = [i for i, s in enumerate(stems) if s == ""]
    assert len(distractor_pos) == 1
    assert "Distractor" in answers[distractor_pos[0]]

    body = q.findtext("questiontext/text") or ""
    assert "<matchInteraction" not in body
    assert "Match the items." in body


def test_cloze_translation_well_formed(tmp_path: Path) -> None:
    p = tmp_path / "c.xml"
    p.write_text(CLOZE_ITEM)
    a = parse_item(p)
    assert classify(a).target == "cloze"
    out = cloze.translate(a, assets=[], category_path=["Algebra"])
    root = _wrap_and_parse(out)
    q = root.find("question")
    assert q.get("type") == "cloze"

    body = q.findtext("questiontext/text") or ""
    # Two cloze markers, one per inlineChoice
    assert body.count("{1:MULTICHOICE:") == 2
    # First gap: correct is f_1 (ID_1), wrong is f_2 — both must be present,
    # with f_1 marked with =. ONYX math `$$..$$` was rewritten to `\(..\)`.
    assert "=\\(f_1\\)" in body
    assert "\\(f_2\\)" in body
    # Second gap: correct is f_3 (ID_3)
    assert "=\\(f_3\\)" in body
    # No QTI tag leakage
    assert "<inlineChoiceInteraction" not in body


def test_cloze_option_escaping(tmp_path: Path) -> None:
    """Cloze options containing ~, #, } must be backslash-escaped or Moodle's
    cloze tokeniser splits the marker mid-option."""
    raw = """<?xml version="1.0" encoding="UTF-8"?>
<assessmentItem xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1"
                identifier="ix" title="x" adaptive="false" timeDependent="false">
  <responseDeclaration identifier="R1" cardinality="single" baseType="identifier">
    <correctResponse><value>A</value></correctResponse>
  </responseDeclaration>
  <responseDeclaration identifier="R2" cardinality="single" baseType="identifier">
    <correctResponse><value>A</value></correctResponse>
  </responseDeclaration>
  <itemBody>
    <p>Gap <inlineChoiceInteraction responseIdentifier="R1" shuffle="false">
      <inlineChoice identifier="A">good</inlineChoice>
      <inlineChoice identifier="B">has~tilde</inlineChoice>
      <inlineChoice identifier="C">has#hash</inlineChoice>
    </inlineChoiceInteraction> and <inlineChoiceInteraction responseIdentifier="R2" shuffle="false">
      <inlineChoice identifier="A">ok</inlineChoice>
      <inlineChoice identifier="B">x</inlineChoice>
    </inlineChoiceInteraction>.</p>
  </itemBody>
</assessmentItem>
"""
    p = tmp_path / "x.xml"
    p.write_text(raw)
    a = parse_item(p)
    out = cloze.translate(a, assets=[], category_path=[])
    body = etree.fromstring(
        f'<?xml version="1.0"?><quiz>{out}</quiz>'.encode()
    ).findtext("question/questiontext/text") or ""
    assert "has\\~tilde" in body
    assert "has\\#hash" in body
