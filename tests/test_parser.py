"""Tests for the QTI 2.1 / ONYX parser."""
from __future__ import annotations

from pathlib import Path

from onyx2moodle.parser import parse_item


CHOICE_ITEM = """<?xml version="1.0" encoding="UTF-8"?>
<assessmentItem xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1"
                identifier="ix" title="Test Choice" adaptive="false" timeDependent="false">
  <responseDeclaration identifier="RESPONSE_1" cardinality="single" baseType="identifier">
    <correctResponse><value>ID_2</value></correctResponse>
  </responseDeclaration>
  <itemBody>
    <p>Pick the right one.</p>
    <choiceInteraction responseIdentifier="RESPONSE_1" shuffle="true" maxChoices="1">
      <simpleChoice identifier="ID_1"><p>Wrong</p></simpleChoice>
      <simpleChoice identifier="ID_2"><p>Right</p></simpleChoice>
    </choiceInteraction>
  </itemBody>
</assessmentItem>
"""

STACK_ITEM = """<?xml version="1.0" encoding="UTF-8"?>
<assessmentItem xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1"
                identifier="ix" title="Test Maxima" adaptive="false" timeDependent="false">
  <responseDeclaration identifier="RESPONSE_1" cardinality="single" baseType="string"/>
  <outcomeDeclaration identifier="MAXSCORE_RESPONSE_1" cardinality="single" baseType="float" view="testConstructor">
    <defaultValue><value>1</value></defaultValue>
  </outcomeDeclaration>
  <templateProcessing>
    <setCorrectResponse identifier="RESPONSE_1">
      <customOperator definition="VARIABLESTRING" value="set(1,3,5,7)"/>
    </setCorrectResponse>
  </templateProcessing>
  <itemBody>
    <p>Enter the set of odd primes &lt; 8:
      <textEntryInteraction responseIdentifier="RESPONSE_1" class="maxima-formula" expectedLength="20"/>
    </p>
  </itemBody>
  <responseProcessing>
    <responseCondition>
      <responseIf>
        <customOperator definition="MAXIMA" value="is(equal(ev($(1)),ev($(2))));">
          <variable identifier="RESPONSE_1"/>
          <correct identifier="RESPONSE_1"/>
        </customOperator>
        <setOutcomeValue identifier="SCORE_RESPONSE_1">
          <variable identifier="MAXSCORE_RESPONSE_1"/>
        </setOutcomeValue>
      </responseIf>
    </responseCondition>
  </responseProcessing>
</assessmentItem>
"""


def test_parse_choice_item(tmp_path: Path) -> None:
    p = tmp_path / "item.xml"
    p.write_text(CHOICE_ITEM)
    a = parse_item(p)
    assert a.title == "Test Choice"
    assert len(a.interactions) == 1
    ix = a.interactions[0]
    assert ix.kind == "choice"
    assert len(ix.choices) == 2
    rd = a.response_decls["RESPONSE_1"]
    assert rd.correct_values == ["ID_2"]


def test_parse_stack_item(tmp_path: Path) -> None:
    p = tmp_path / "item.xml"
    p.write_text(STACK_ITEM)
    a = parse_item(p)
    assert a.has_maxima_grading()
    assert a.has_template_processing()
    assert a.template_bindings[0].custom_op == "VARIABLESTRING"
    assert a.template_bindings[0].value == "set(1,3,5,7)"
    assert a.grading_rules[0].response_identifier == "RESPONSE_1"
    assert a.grading_rules[0].custom_op == "MAXIMA"
    assert a.grading_rules[0].max_score == 1.0
