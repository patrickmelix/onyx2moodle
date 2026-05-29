"""Unpack OPAL ONYX exports into a flat tree of question items.

OPAL exports are nested zips:

    outer.zip
      Algebra/Gruppentheorie/Gruppenaxiome_3.zip
        imsmanifest.xml
        id<uuid>.xml          (the QTI 2.1 assessment item)
        *.png / *.jpg         (embedded media, optional)

This module flattens that to:

    work/
      <slug>/
        item.xml
        manifest.xml
        assets/*.png
        _meta.json            (category path, source filename)
"""
from __future__ import annotations

import json
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

_SLUG_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _slugify(name: str) -> str:
    return _SLUG_RE.sub("_", name).strip("_")


@dataclass
class UnpackedItem:
    """One ONYX assessment item, unpacked and ready to translate."""

    slug: str
    item_xml: Path
    manifest_xml: Path | None
    assets: list[Path] = field(default_factory=list)
    category_path: list[str] = field(default_factory=list)
    source_archive: str = ""
    source_member: str = ""

    def meta_dict(self) -> dict:
        return {
            "slug": self.slug,
            "category_path": self.category_path,
            "source_archive": self.source_archive,
            "source_member": self.source_member,
            "assets": [a.name for a in self.assets],
        }


def _unpack_inner(inner_bytes: bytes, item_dir: Path) -> tuple[Path | None, Path | None, list[Path]]:
    """Extract one inner zip's payload into item_dir. Returns (item_xml, manifest_xml, assets)."""
    item_xml: Path | None = None
    manifest_xml: Path | None = None
    assets: list[Path] = []
    assets_dir = item_dir / "assets"
    with zipfile.ZipFile(BytesIO(inner_bytes)) as inner:
        for inner_member in inner.namelist():
            if inner_member.endswith("/"):
                continue
            target_name = Path(inner_member).name
            if inner_member == "imsmanifest.xml":
                manifest_xml = item_dir / "manifest.xml"
                manifest_xml.write_bytes(inner.read(inner_member))
            elif target_name.lower().endswith(".xml"):
                item_xml = item_dir / "item.xml"
                item_xml.write_bytes(inner.read(inner_member))
            else:
                assets_dir.mkdir(exist_ok=True)
                out = assets_dir / target_name
                out.write_bytes(inner.read(inner_member))
                assets.append(out)
    return item_xml, manifest_xml, assets


def unpack_archive(outer_zip: Path, work_dir: Path) -> list[UnpackedItem]:
    """Unpack one OPAL outer zip into work_dir/<slug>/ trees.

    The inner directory structure is preserved as `category_path`
    (used later for Moodle category headers).
    """
    outer_zip = Path(outer_zip).resolve()
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    items: list[UnpackedItem] = []

    with zipfile.ZipFile(outer_zip) as outer:
        for member in outer.namelist():
            if not member.endswith(".zip"):
                continue
            parts = Path(member).parts
            category = [p for p in parts[:-1] if p not in ("", ".")]
            stem = Path(member).stem
            slug = _slugify(stem) or "item"

            item_dir = work_dir / slug
            n = 1
            while item_dir.exists():
                n += 1
                item_dir = work_dir / f"{slug}_{n}"
            item_dir.mkdir(parents=True)

            try:
                item_xml, manifest_xml, assets = _unpack_inner(outer.read(member), item_dir)
            except zipfile.BadZipFile:
                shutil.rmtree(item_dir, ignore_errors=True)
                continue

            if item_xml is None:
                shutil.rmtree(item_dir, ignore_errors=True)
                continue

            item = UnpackedItem(
                slug=slug,
                item_xml=item_xml,
                manifest_xml=manifest_xml,
                assets=assets,
                category_path=category,
                source_archive=outer_zip.name,
                source_member=member,
            )
            (item_dir / "_meta.json").write_text(
                json.dumps(item.meta_dict(), indent=2), encoding="utf-8"
            )
            items.append(item)

    return items
