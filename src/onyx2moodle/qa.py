"""Optional structural QA gate for emitted STACK questions.

If a STACK question validator is available on the system (any script that
takes a single Moodle XML file containing one `<question type="stack">` and
exits 0 on pass, non-zero on fail, printing one `[WARN]` / `[FAIL]` line per
finding), we can run it as a post-emission check.

Discovery order:
  1. `$ONYX2MOODLE_VALIDATOR` environment variable
  2. `validate.py` on `PATH`

The validator itself is not bundled — supply your own, or skip `--validate`.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


_STACK_QUESTION_RE = re.compile(
    r'<question type="stack">.*?</question>', re.DOTALL
)


@dataclass
class ValidationResult:
    question_name: str
    passed: bool
    output: str
    warnings: int
    errors: int


def find_validator() -> Path | None:
    """Locate an external STACK validator script, or return None."""
    env = os.environ.get("ONYX2MOODLE_VALIDATOR")
    if env and Path(env).exists():
        return Path(env)
    w = shutil.which("validate.py")
    return Path(w) if w else None


def validate_bundle(bundle_xml: Path, validator: Path | None = None) -> list[ValidationResult]:
    """Run the validator on every STACK question in the bundle.

    Non-STACK questions are skipped (the validator is STACK-specific).
    """
    validator = validator or find_validator()
    if validator is None:
        raise RuntimeError(
            "No STACK validator found; set $ONYX2MOODLE_VALIDATOR or put validate.py on PATH."
        )
    content = Path(bundle_xml).read_text(encoding="utf-8")
    results: list[ValidationResult] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for i, m in enumerate(_STACK_QUESTION_RE.finditer(content)):
            block = m.group(0)
            # Extract name for reporting
            name_m = re.search(r"<name>\s*<text>([^<]+)</text>", block)
            name = (name_m.group(1) if name_m else f"q{i}").strip()
            wrapped = f'<?xml version="1.0" encoding="UTF-8"?>\n<quiz>\n{block}\n</quiz>\n'
            f = Path(tmpdir) / f"q{i:04d}.xml"
            f.write_text(wrapped, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(validator), str(f)],
                capture_output=True, text=True,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            warnings = out.count("[WARN]")
            errors = out.count("[FAIL]")
            results.append(ValidationResult(
                question_name=name,
                passed=(proc.returncode == 0),
                output=out,
                warnings=warnings,
                errors=errors,
            ))
    return results
