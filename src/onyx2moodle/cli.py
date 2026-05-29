"""Command-line interface.

Subcommands:

    onyx2moodle unpack    <outer.zip> --work <dir>
    onyx2moodle inventory <outer.zip> [--work <dir>]
    onyx2moodle convert   <outer.zip> -o <bundle.xml> [--validate]

`unpack` and `inventory` are read-only and idempotent (per-run work dir).
`convert` does unpack + parse + classify + translate + emit + (optional) validate.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from . import __version__
from .classifier import classify
from .emitter import emit_bundle
from .parser import parse_item
from .qa import find_validator, validate_bundle
from .translate import cloze, essay, matching, multichoice, shortanswer, stack
from .unpack import unpack_archive


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="onyx2moodle", description="ONYX QTI 2.1 -> Moodle XML")
    p.add_argument("--version", action="version", version=f"onyx2moodle {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sp_unpack = sub.add_parser("unpack", help="Extract an outer ONYX zip into a flat tree")
    sp_unpack.add_argument("archive", type=Path)
    sp_unpack.add_argument("--work", type=Path, default=Path("./work"))

    sp_inv = sub.add_parser("inventory", help="Classify items in an archive and print a coverage report")
    sp_inv.add_argument("archive", type=Path)
    sp_inv.add_argument("--work", type=Path, default=Path("./work"))
    sp_inv.add_argument("--json", action="store_true", help="Emit raw JSON instead of the text report")

    sp_conv = sub.add_parser("convert", help="Convert an archive to a single Moodle XML bundle")
    sp_conv.add_argument("archive", type=Path)
    sp_conv.add_argument("-o", "--output", type=Path, required=True)
    sp_conv.add_argument("--work", type=Path, default=Path("./work"))
    sp_conv.add_argument("--validate", action="store_true",
                         help="Run an external STACK validator on each STACK question "
                              "(see ONYX2MOODLE_VALIDATOR)")
    sp_conv.add_argument("--course-root", default="$course$/top",
                         help="Moodle category prefix (default: $course$/top)")
    sp_conv.add_argument("--only", choices=["stack", "multichoice", "shortanswer", "essay", "matching", "cloze"],
                         action="append", default=None,
                         help="Restrict conversion to these targets (repeatable)")
    return p


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_unpack(args: argparse.Namespace) -> int:
    items = unpack_archive(args.archive, args.work / args.archive.stem)
    print(f"Unpacked {len(items)} items to {args.work / args.archive.stem}")
    return 0


def cmd_inventory(args: argparse.Namespace) -> int:
    items = unpack_archive(args.archive, args.work / args.archive.stem)
    rows: list[dict] = []
    for it in items:
        try:
            ai = parse_item(it.item_xml)
            cls = classify(ai)
            rows.append({
                "slug": it.slug,
                "title": ai.title,
                "interactions": [i.kind for i in ai.interactions],
                "maxima": ai.has_maxima_grading(),
                "template": ai.has_template_processing(),
                "target": cls.target,
                "reason": cls.reason,
                "convertible": cls.convertible,
                "category_path": it.category_path,
            })
        except Exception as e:
            rows.append({
                "slug": it.slug,
                "title": "",
                "error": str(e),
                "target": "error",
                "convertible": False,
            })

    if args.json:
        json.dump(rows, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    targets = Counter(r["target"] for r in rows)
    n = len(rows)
    convertible = sum(1 for r in rows if r.get("convertible"))
    print(f"\nInventory: {args.archive.name}  ({n} items)")
    print(f"  Convertible automatically: {convertible} ({100*convertible//max(1,n)}%)")
    print(f"\n  Target distribution:")
    for target, count in targets.most_common():
        print(f"    {target:14s} {count:4d}")
    print(f"\n  Manual / deferred items (top 10 reasons):")
    deferred_reasons = Counter(
        r.get("reason", "") for r in rows if not r.get("convertible") and r["target"] != "error"
    )
    for reason, count in deferred_reasons.most_common(10):
        print(f"    {count:4d}  {reason}")
    errors = [r for r in rows if r["target"] == "error"]
    if errors:
        print(f"\n  Parse errors: {len(errors)}")
        for r in errors[:5]:
            print(f"    {r['slug']}: {r.get('error', '')[:80]}")
    return 0


def cmd_convert(args: argparse.Namespace) -> int:
    items = unpack_archive(args.archive, args.work / args.archive.stem)
    fragments: list[tuple[list[str], str]] = []
    skipped: list[tuple[str, str]] = []
    translators = {
        "stack": stack.translate,
        "multichoice": multichoice.translate,
        "shortanswer": shortanswer.translate,
        "essay": essay.translate,
        "matching": matching.translate,
        "cloze": cloze.translate,
    }
    allowed = set(args.only) if args.only else None

    for it in items:
        try:
            ai = parse_item(it.item_xml)
        except Exception as e:
            skipped.append((it.slug, f"parse error: {e}"))
            continue
        cls = classify(ai)
        target = cls.target
        if allowed and target not in allowed:
            skipped.append((it.slug, f"filtered out (target={target})"))
            continue
        translator = translators.get(target)
        if translator is None:
            skipped.append((it.slug, f"no translator yet (target={target}, {cls.reason})"))
            continue
        try:
            block = translator(ai, assets=it.assets, category_path=it.category_path)
            fragments.append((it.category_path, block))
        except Exception as e:
            skipped.append((it.slug, f"translator error: {e}"))

    out_path = emit_bundle(fragments, args.output, course_root=args.course_root)
    print(f"Wrote {len(fragments)} questions to {out_path}")
    print(f"Skipped {len(skipped)} items.")
    if skipped:
        skip_log = out_path.with_suffix(".skipped.log")
        skip_log.write_text(
            "\n".join(f"{slug}\t{reason}" for slug, reason in skipped),
            encoding="utf-8",
        )
        print(f"  -> details in {skip_log}")

    if args.validate:
        validator = find_validator()
        if validator is None:
            print("WARN: --validate requested but no validator found "
                  "(set ONYX2MOODLE_VALIDATOR or put validate.py on PATH)")
            return 0
        print(f"\nRunning validator: {validator}")
        results = validate_bundle(out_path, validator)
        passed = sum(1 for r in results if r.passed)
        warns = sum(r.warnings for r in results)
        errs = sum(r.errors for r in results)
        print(f"Validator: {passed}/{len(results)} passed, {warns} warnings, {errs} errors")
        if errs:
            print("\nFailures (first 5):")
            for r in [r for r in results if not r.passed][:5]:
                print(f"\n--- {r.question_name} ---")
                print(r.output)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "unpack":
        return cmd_unpack(args)
    if args.command == "inventory":
        return cmd_inventory(args)
    if args.command == "convert":
        return cmd_convert(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
