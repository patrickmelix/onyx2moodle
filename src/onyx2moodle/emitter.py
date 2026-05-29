"""Bundle translated question fragments into one importable Moodle XML file.

Emits:

    <?xml version="1.0" encoding="UTF-8"?>
    <quiz>
      <!-- category headers, then question blocks, grouped by category path -->
    </quiz>

Per-category headers follow the convention seen in the workspace's existing
Moodle XMLs:

    <question type="category">
      <category><text>$course$/top/Algebra/Gruppentheorie</text></category>
      <info format="html"><text></text></info>
      <idnumber></idnumber>
    </question>
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .translate.common import to_category_path


def emit_bundle(
    fragments: list[tuple[list[str], str]],
    output_path: Path,
    course_root: str = "$course$/top",
) -> Path:
    """Group `fragments` by category path and write the Moodle XML bundle.

    Each fragment is `(category_path, question_xml_block)`.
    """
    by_category: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for cat, frag in fragments:
        by_category[tuple(cat)].append(frag)

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<quiz>',
    ]
    for cat in sorted(by_category):
        cat_str = to_category_path(list(cat), root=course_root)
        parts.append(_category_header(cat_str))
        for frag in by_category[cat]:
            parts.append(frag)
    parts.append('</quiz>')

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return output_path


def _category_header(path: str) -> str:
    return f"""<!-- category header -->
  <question type="category">
    <category><text>{_escape(path)}</text></category>
    <info format="html"><text></text></info>
    <idnumber></idnumber>
  </question>"""


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
