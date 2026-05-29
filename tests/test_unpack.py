"""Smoke tests for the nested-zip unpacker."""
from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from onyx2moodle.unpack import unpack_archive


def _make_inner_zip(item_xml: str, manifest_xml: str = "<manifest/>") -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("imsmanifest.xml", manifest_xml)
        zf.writestr("idabc-123.xml", item_xml)
    return buf.getvalue()


def _make_outer_zip(members: dict[str, bytes]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_unpack_preserves_category_path(tmp_path: Path) -> None:
    inner = _make_inner_zip("<assessmentItem/>")
    outer = _make_outer_zip({
        "Algebra/Gruppentheorie/Q1.zip": inner,
        "Analysis/Folgen/Q2.zip": inner,
    })
    outer_path = tmp_path / "outer.zip"
    outer_path.write_bytes(outer)

    items = unpack_archive(outer_path, tmp_path / "work")
    assert len(items) == 2
    cats = sorted(it.category_path for it in items)
    assert cats == [["Algebra", "Gruppentheorie"], ["Analysis", "Folgen"]]
    for it in items:
        assert it.item_xml.exists()
        assert it.item_xml.read_text() == "<assessmentItem/>"


def test_unpack_skips_non_zip_members(tmp_path: Path) -> None:
    inner = _make_inner_zip("<assessmentItem/>")
    outer = _make_outer_zip({
        "Algebra/Q1.zip": inner,
        "Algebra/README.txt": b"hello",
    })
    outer_path = tmp_path / "outer.zip"
    outer_path.write_bytes(outer)

    items = unpack_archive(outer_path, tmp_path / "work")
    assert len(items) == 1


def test_unpack_skips_bad_inner_zip(tmp_path: Path) -> None:
    outer = _make_outer_zip({
        "Algebra/Q1.zip": b"this is not a zip",
        "Algebra/Q2.zip": _make_inner_zip("<assessmentItem/>"),
    })
    outer_path = tmp_path / "outer.zip"
    outer_path.write_bytes(outer)

    items = unpack_archive(outer_path, tmp_path / "work")
    assert len(items) == 1
    assert items[0].slug == "Q2"
