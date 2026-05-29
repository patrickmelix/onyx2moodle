"""Tests for the per-item classifier routing."""
from __future__ import annotations

from pathlib import Path

from onyx2moodle.classifier import classify
from onyx2moodle.parser import parse_item

from .test_parser import CHOICE_ITEM, STACK_ITEM

PRINTED_VAR_ITEM = """<?xml version="1.0" encoding="UTF-8"?>
<assessmentItem xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1"
                identifier="ix" title="Variant" adaptive="false" timeDependent="false">
  <responseDeclaration identifier="RESPONSE_1" cardinality="single" baseType="string"/>
  <templateProcessing>
    <setCorrectResponse identifier="RESPONSE_1">
      <customOperator definition="VARIABLESTRING" value="$(1)">
        <variable identifier="sol"/>
      </customOperator>
    </setCorrectResponse>
  </templateProcessing>
  <itemBody>
    <p>Solve <printedVariable identifier="ftex"/> = 0.
      <textEntryInteraction responseIdentifier="RESPONSE_1" class="maxima-formula"/>
    </p>
  </itemBody>
</assessmentItem>
"""


def test_classify_choice_targets_multichoice(tmp_path: Path) -> None:
    p = tmp_path / "i.xml"
    p.write_text(CHOICE_ITEM)
    cls = classify(parse_item(p))
    assert cls.target == "multichoice"
    assert cls.convertible is True


def test_classify_simple_stack_targets_stack(tmp_path: Path) -> None:
    p = tmp_path / "i.xml"
    p.write_text(STACK_ITEM)
    cls = classify(parse_item(p))
    assert cls.target == "stack"
    assert cls.convertible is True


def test_classify_variant_defers_to_manual(tmp_path: Path) -> None:
    p = tmp_path / "i.xml"
    p.write_text(PRINTED_VAR_ITEM)
    cls = classify(parse_item(p))
    assert cls.target == "manual"
    assert cls.convertible is False
    assert "template variants" in cls.reason or "printedVariable" in cls.reason


# Variant metadata co-existing with a non-textEntry interaction (matchInteraction
# with printedVariable in the source labels) — must route to its translator, not
# defer. This is the Permutationen_Anwendung_I pattern that the old classifier
# mis-deferred to manual.
MATCH_WITH_PRINTED_VAR_ITEM = """<?xml version="1.0" encoding="UTF-8"?>
<assessmentItem xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1"
                identifier="ix" title="Match w/ vars" adaptive="false" timeDependent="false">
  <responseDeclaration identifier="RESPONSE_1" cardinality="multiple" baseType="directedPair">
    <correctResponse><value>S1 T1</value><value>S2 T2</value></correctResponse>
  </responseDeclaration>
  <templateProcessing>
    <setCorrectResponse identifier="RESPONSE_1">
      <customOperator definition="VARIABLESTRING" value="$(1)">
        <variable identifier="sol"/>
      </customOperator>
    </setCorrectResponse>
  </templateProcessing>
  <itemBody>
    <p>Match these (random label: <printedVariable identifier="label"/>):</p>
    <matchInteraction responseIdentifier="RESPONSE_1" shuffle="true" maxAssociations="0">
      <simpleMatchSet>
        <simpleAssociableChoice identifier="S1" matchMax="1"><p>Stem A</p></simpleAssociableChoice>
        <simpleAssociableChoice identifier="S2" matchMax="1"><p>Stem B</p></simpleAssociableChoice>
      </simpleMatchSet>
      <simpleMatchSet>
        <simpleAssociableChoice identifier="T1" matchMax="1"><p>Answer 1</p></simpleAssociableChoice>
        <simpleAssociableChoice identifier="T2" matchMax="1"><p>Answer 2</p></simpleAssociableChoice>
      </simpleMatchSet>
    </matchInteraction>
  </itemBody>
</assessmentItem>
"""


def test_classify_match_with_template_metadata_routes_to_matching(tmp_path: Path) -> None:
    p = tmp_path / "i.xml"
    p.write_text(MATCH_WITH_PRINTED_VAR_ITEM)
    cls = classify(parse_item(p))
    assert cls.target == "matching"
    assert cls.convertible is True
