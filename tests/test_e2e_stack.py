"""End-to-end test: parse a simple STACK ONYX item, translate, validate well-formedness."""
from __future__ import annotations

from pathlib import Path

from lxml import etree

from onyx2moodle.parser import parse_item
from onyx2moodle.translate import stack

from .test_parser import STACK_ITEM


def test_stack_translation_is_well_formed(tmp_path: Path) -> None:
    p = tmp_path / "i.xml"
    p.write_text(STACK_ITEM)
    a = parse_item(p)
    out = stack.translate(a, assets=[], category_path=["Algebra", "Mengen"])
    # Wrap in <quiz> envelope and parse with lxml — catches all malformed-XML bugs
    wrapped = f'<?xml version="1.0" encoding="UTF-8"?>\n<quiz>\n{out}\n</quiz>\n'
    root = etree.fromstring(wrapped.encode())
    qs = root.findall("question")
    assert len(qs) == 1
    assert qs[0].get("type") == "stack"

    # Sanity: tans should be the Maxima-translated set literal
    qv = qs[0].findtext("questionvariables/text") or ""
    assert "{1,3,5,7}" in qv

    # Sanity: the input placeholder is in the body
    body = qs[0].findtext("questiontext/text") or ""
    assert "[[input:ans1]]" in body
    assert "[[validation:ans1]]" in body
    # No QTI tag leakage
    assert "<textEntryInteraction" not in body
    assert "<printedVariable" not in body
    assert "<customOperator" not in body

    # The TE:1 tag is derived from the first category segment
    tags = [t.findtext("text") for t in qs[0].findall("tags/tag")]
    assert any(t == "TE:1:Algebra" for t in tags)
