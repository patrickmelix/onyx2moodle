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
