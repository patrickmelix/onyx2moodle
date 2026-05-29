"""Tests for the built-in STACK Maxima lint in qa.py.

The lint catches sandbox pitfalls (forbidden load/random, bare combinatorics
calls, String testoptions misuse) that pass any structural validator but fail
at Moodle import. Each test exercises one rule and confirms it (a) fires on
the broken pattern, (b) does NOT fire on the corrected pattern.
"""
from __future__ import annotations

from pathlib import Path

from onyx2moodle.qa import lint_stack_maxima

# A minimal STACK question shell with replaceable questionvariables / nodes.
# The lint walks the actual XML structure, so we keep this realistic enough
# that the regex patterns find the right blocks.
def _shell(qvars: str, prt_node: str = "", name: str = "Test Q") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<quiz>
<question type="stack">
  <name><text>{name}</text></name>
  <questionvariables><text>{qvars}</text></questionvariables>
  <prt>
    <name>prt_ans1</name>
    {prt_node}
  </prt>
</question>
</quiz>
"""


def test_lint_flags_load_call(tmp_path: Path) -> None:
    f = tmp_path / "q.xml"
    f.write_text(_shell('load("combinatorics");\nP : random_permutation(7);'))
    report = lint_stack_maxima(f)
    assert not report.ok
    rules = [x.rule for x in report.findings]
    assert "forbidden-load" in rules


def test_lint_passes_clean_questionvariables(tmp_path: Path) -> None:
    """A question with only allowed primitives must report no findings."""
    f = tmp_path / "q.xml"
    f.write_text(_shell(
        'a : rand(15) + 5;\n'
        'tans1 : mod(inv_mod(a, 17), 17);'
    ))
    report = lint_stack_maxima(f)
    assert report.ok, f"unexpected findings: {report.summary()}"


def test_lint_flags_bare_random_call(tmp_path: Path) -> None:
    f = tmp_path / "q.xml"
    f.write_text(_shell('x : random(10);'))
    report = lint_stack_maxima(f)
    assert not report.ok
    assert any(x.rule == "forbidden-random" for x in report.findings)


def test_lint_allows_random_permutation_and_other_random_underscore(tmp_path: Path) -> None:
    """`random_permutation`, `random_subset`, etc. are STACK-allowed; the lint
    must distinguish them from the bare `random(...)` it forbids."""
    f = tmp_path / "q.xml"
    f.write_text(_shell(
        'P : random_permutation(makelist(i, i, 1, 7));\n'
        'S : random_subset({1,2,3,4,5});\n'
        'T : random_subset_n({1,2,3,4,5,6,7}, 3);'
    ))
    report = lint_stack_maxima(f)
    assert report.ok, f"false positives: {report.summary()}"


def test_lint_flags_random_even_when_random_underscore_present_on_same_line(tmp_path: Path) -> None:
    """Regression: a line containing both `random(...)` AND `random_permutation(...)`
    must still flag the forbidden bare call. An earlier implementation suppressed
    the finding because a line-level guard looked for `random_<x>(` anywhere on
    the line and treated that as a global escape hatch."""
    f = tmp_path / "q.xml"
    f.write_text(_shell(
        'x : random(10); P : random_permutation(makelist(i, i, 1, 5));'
    ))
    report = lint_stack_maxima(f)
    assert any(x.rule == "forbidden-random" for x in report.findings), report.summary()


def test_lint_flags_bare_combinatorics_calls(tmp_path: Path) -> None:
    f = tmp_path / "q.xml"
    f.write_text(_shell(
        'P : random_permutation(makelist(i, i, 1, 7));\n'
        'Pc : perm_cycles(P);\n'
        'Q : permult(P, P);'
    ))
    report = lint_stack_maxima(f)
    rules = [x.rule for x in report.findings]
    assert rules.count("unloaded-combinatorics") == 2
    msgs = " ".join(x.message for x in report.findings)
    assert "perm_cycles" in msgs and "permult" in msgs


def test_lint_allows_stk_prefixed_combinatorics(tmp_path: Path) -> None:
    """The `stk_` prefix is the documented fix; lint must accept it."""
    f = tmp_path / "q.xml"
    f.write_text(_shell(
        'P : random_permutation(makelist(i, i, 1, 7));\n'
        'Pc : stk_perm_cycles(P);\n'
        'Q : stk_permult(P, P);'
    ))
    report = lint_stack_maxima(f)
    assert report.ok, f"false positives on stk_-prefixed names: {report.summary()}"


def test_lint_flags_string_test_with_testoptions(tmp_path: Path) -> None:
    """`<answertest>String</answertest>` paired with non-empty `<testoptions>`
    is the case-insensitive-guess misuse pattern. Empty `<testoptions/>` and
    `<testoptions></testoptions>` are OK."""
    bad_node = (
        '<answertest>String</answertest>'
        '<sans>ans1</sans>'
        '<tans>"Ja"</tans>'
        '<testoptions>1</testoptions>'
    )
    f = tmp_path / "q.xml"
    f.write_text(_shell('tans1 : "Ja";', prt_node=bad_node))
    report = lint_stack_maxima(f)
    assert any(x.rule == "string-test-testoptions" for x in report.findings)


def test_lint_allows_string_test_with_empty_testoptions(tmp_path: Path) -> None:
    ok_node = (
        '<answertest>String</answertest>'
        '<sans>ans1</sans>'
        '<tans>"Ja"</tans>'
        '<testoptions/>'
    )
    f = tmp_path / "q.xml"
    f.write_text(_shell('tans1 : "Ja";', prt_node=ok_node))
    report = lint_stack_maxima(f)
    assert report.ok, f"false positive on empty testoptions: {report.summary()}"


def test_lint_skips_mentions_inside_comments(tmp_path: Path) -> None:
    """Words inside `/* ... */` comments must not trigger findings."""
    f = tmp_path / "q.xml"
    f.write_text(_shell(
        '/* Note: combinatorics functions perm_cycles / permult are NOT */\n'
        '/* loaded by stackmaxima.mac; use stk_-prefixed inline versions. */\n'
        'tans1 : 42;'
    ))
    report = lint_stack_maxima(f)
    assert report.ok, f"false positives on comments: {report.summary()}"
