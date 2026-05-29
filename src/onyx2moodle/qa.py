"""Optional structural QA gate for emitted STACK questions.

Two complementary checks live here:

1. **External structural validator** (`validate_bundle`). Any script that takes
   one Moodle XML file with a single `<question type="stack">` and exits 0 on
   pass / non-zero on fail, printing `[WARN]` / `[FAIL]` lines. Catches PRT
   graph cycles, defaultgrade vs PRT-value-sum mismatch, etc.

   Discovery order: `$ONYX2MOODLE_VALIDATOR`, then `validate.py` on `PATH`.

2. **Built-in STACK Maxima lint** (`lint_stack_maxima`). Pattern-based check
   for `<questionvariables>` content that would pass the structural validator
   but fail at Moodle import because STACK's Maxima sandbox rejects:

   - `load("...")` — security: forbidden in question scope.
   - `random(` — STACK uses `rand(`. Calling `random` errors with
     "Verbotene Funktion: random."
   - Bare names of unloaded combinatorics functions (`perm_cycles`, `permult`,
     `perm_parity`, `inv_perm`) when not prefixed with `stk_`. The
     combinatorics package is NOT loaded by `stackmaxima.mac`; calling these
     names directly errors with "Verbotene Funktion: ...".
   - `<answertest>String</answertest>` paired with a non-empty
     `<testoptions>` — STACK's String test has no case-insensitive option
     and ignores testoptions, so this is dead config that misleads readers.

   See `~/.claude/projects/-workspace/memory/reference_stack_maxima_sandbox.md`
   for the full sandbox reference.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

_STACK_QUESTION_RE = re.compile(
    r'<question type="stack">.*?</question>', re.DOTALL
)
_QUESTIONVARS_RE = re.compile(
    r'<questionvariables>\s*<text>(.*?)</text>\s*</questionvariables>', re.DOTALL
)
_STRING_TEST_WITH_TESTOPTIONS_RE = re.compile(
    r'<answertest>String(?:Sloppy)?</answertest>'
    r'\s*<sans>[^<]*</sans>\s*<tans>[^<]*</tans>'
    r'\s*<testoptions>\s*(\S[^<]*)\s*</testoptions>',
    re.DOTALL,
)

# Combinatorics functions NOT auto-loaded by stackmaxima.mac. If a STACK
# question calls these bare names, it fails at Moodle import. The fix is
# to inline them under a `stk_` prefix — see `feedback_stack_stk_prefix.md`.
_COMBINATORICS_BARE_NAMES = (
    "perm_cycles",
    "permult",
    "perm_parity",
    "inv_perm",
    "perm_decomp",
    "permp",
)
# `random_permutation` IS a Maxima base built-in (not from `combinatorics`)
# and is allowed — but calling `random` (without _permutation) is forbidden.
_BARE_RANDOM_RE = re.compile(r'(?<![_a-zA-Z])random\s*\(')
_LOAD_RE = re.compile(r'(?<![_a-zA-Z])load\s*\(')


@dataclass
class ValidationResult:
    question_name: str
    passed: bool
    output: str
    warnings: int
    errors: int


@dataclass
class LintFinding:
    question_name: str
    rule: str
    line: int                       # 1-based line within the question block
    snippet: str                    # the offending line, trimmed
    message: str

    def format(self) -> str:
        return f"[{self.rule}] {self.question_name} line {self.line}: {self.message}\n    {self.snippet}"


@dataclass
class LintReport:
    findings: list[LintFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def summary(self) -> str:
        if self.ok:
            return "STACK Maxima lint: 0 findings"
        return f"STACK Maxima lint: {len(self.findings)} finding(s)\n" + "\n".join(
            f.format() for f in self.findings
        )


def _bare_combinatorics_re(name: str) -> re.Pattern[str]:
    """Match `<name>(` not preceded by an underscore or letter.

    The negative lookbehind excludes `stk_perm_cycles(`, `my_permult(`, etc.
    A separate guard rejects `random_permutation` (which contains `perm` as a
    substring but isn't one of our targets).
    """
    return re.compile(rf'(?<![_a-zA-Z]){re.escape(name)}\s*\(')


def _scan_questionvariables(qvars_text: str, qname: str) -> list[LintFinding]:
    findings: list[LintFinding] = []
    lines = qvars_text.splitlines()
    for idx, raw_line in enumerate(lines, start=1):
        # Skip pure comment lines so mentions inside `/* ... */` don't false-positive.
        # Maxima block comments span lines; conservatively drop everything between
        # `/*` and `*/` on the same line for the regex check, but keep the line for snippet.
        line = re.sub(r'/\*.*?\*/', '', raw_line)
        # Strip line-comment continuation: anything from `/*` to end-of-line if
        # the comment doesn't close on the same line (best-effort).
        if '/*' in line and '*/' not in line:
            line = line[: line.index('/*')]
        snippet = raw_line.strip()
        # --- forbidden load(...)
        if _LOAD_RE.search(line):
            findings.append(LintFinding(
                question_name=qname, rule="forbidden-load", line=idx, snippet=snippet,
                message="`load(...)` is forbidden in STACK question scope.",
            ))
        # --- forbidden random(...)  (random_permutation is OK; distinguish)
        for m in _BARE_RANDOM_RE.finditer(line):
            # Skip random_permutation, random_subset, random_subset_n, etc.
            tail = line[m.end():]
            # `random` is followed by `(` per the regex; check what *precedes*
            # the open-paren region for the `_<suffix>` pattern.
            head = line[m.start():m.end()]
            if head.rstrip("(").rstrip().endswith("random"):
                # Now check if it's actually `random_*` by inspecting the source
                # line directly with a stricter pattern.
                if re.search(r'(?<![_a-zA-Z])random\s*\(', line) and not re.search(
                    r'(?<![_a-zA-Z])random_[a-zA-Z_]+\s*\(', line
                ):
                    findings.append(LintFinding(
                        question_name=qname, rule="forbidden-random", line=idx, snippet=snippet,
                        message="`random(...)` is forbidden; STACK uses `rand(...)` for random integers.",
                    ))
                    break
        # --- bare combinatorics calls (not stk_-prefixed)
        for name in _COMBINATORICS_BARE_NAMES:
            if _bare_combinatorics_re(name).search(line):
                findings.append(LintFinding(
                    question_name=qname, rule="unloaded-combinatorics", line=idx, snippet=snippet,
                    message=(
                        f"`{name}(...)` calls a function from the combinatorics package, "
                        f"which is NOT loaded by stackmaxima.mac. Inline it under a `stk_` prefix "
                        f"(e.g. `stk_{name}`). See feedback_stack_stk_prefix.md."
                    ),
                ))
    return findings


def _scan_string_test_misuse(block: str, qname: str) -> list[LintFinding]:
    """`<answertest>String</answertest>` paired with a non-empty `<testoptions>`
    is dead config — STACK ignores testoptions for the String test, so it's
    almost always a misunderstanding (e.g. trying to enable case-insensitive).
    """
    findings: list[LintFinding] = []
    for m in _STRING_TEST_WITH_TESTOPTIONS_RE.finditer(block):
        # Compute approximate line within the block.
        line = block[: m.start()].count("\n") + 1
        findings.append(LintFinding(
            question_name=qname, rule="string-test-testoptions", line=line,
            snippet=m.group(0).replace("\n", " ").strip()[:140],
            message=(
                "`String` answer test has no documented `testoptions` (no case-insensitive option). "
                "For yes/no-style answers prefer numerical 1/0 with AlgEquiv; for free-form text "
                "preprocess casing via `slower(sa)` in feedbackvariables and compare lower-case."
            ),
        ))
    return findings


def lint_stack_maxima(bundle_xml: Path) -> LintReport:
    """Scan every `<question type="stack">` block in `bundle_xml` for known
    Maxima-sandbox pitfalls that would silently pass a structural validator
    but fail at Moodle import.

    Returns a `LintReport` with zero or more `LintFinding`s. Caller decides
    whether to block on findings (non-empty report = recommend not importing).
    """
    content = Path(bundle_xml).read_text(encoding="utf-8")
    report = LintReport()
    for i, m in enumerate(_STACK_QUESTION_RE.finditer(content)):
        block = m.group(0)
        name_m = re.search(r"<name>\s*<text>([^<]+)</text>", block)
        qname = (name_m.group(1) if name_m else f"q{i}").strip()
        qvars_m = _QUESTIONVARS_RE.search(block)
        if qvars_m:
            qvars_text = qvars_m.group(1)
            report.findings.extend(_scan_questionvariables(qvars_text, qname))
        report.findings.extend(_scan_string_test_misuse(block, qname))
    return report


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
