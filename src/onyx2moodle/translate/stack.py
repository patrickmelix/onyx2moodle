"""ONYX (textEntryInteraction + MAXIMA grading) -> Moodle qtype_stack.

Produces a mechanical, *correct* 1-node PRT per response: a single
`AlgEquiv(ans, tans)` test, the teacher answer pulled from
`templateProcessing` (`VARIABLESTRING`) or `responseDeclaration`. No
diagnostic-misconception branches and no qtests — those are out of scope
for a mechanical converter and should be added by hand for individual
high-value questions after import.

The emitted XML imports cleanly into Moodle's `qtype_stack` plugin and
parses against any external STACK structural validator (see `qa.py`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..parser import AssessmentItem, Interaction, ResponseDeclaration
from ..render import load_template, substitute_cdata, substitute_escaped, substitute_raw
from .common import (
    embed_images_as_base64,
    extract_question_html,
)

STACK_VERSION = "2024111900"   # matches the skill's lhopital example


# ---------------------------------------------------------------------------
# Maxima-value coercion: rewrite ONYX VARIABLESTRING / placeholder strings
# into Maxima literals.
# ---------------------------------------------------------------------------


_ONYX_SET_RE = re.compile(r"^set\((.*)\)$", flags=re.DOTALL)


def _onyx_value_to_maxima(value: str) -> str:
    """Convert an ONYX answer literal (the `value=` payload of VARIABLESTRING)
    to a Maxima expression suitable as a STACK teacher-answer (`<tans>`).

    Rules:
      - `set(1,5,7,11)` -> `{1,5,7,11}` (Maxima native set)
      - plain Maxima expressions (`x^2+1`, `[1,2,3]`, `42`) pass through unchanged
      - empty string -> "null" (so PRT comparison fails meaningfully)
    """
    v = (value or "").strip()
    if not v:
        return "null"
    m = _ONYX_SET_RE.match(v)
    if m:
        return "{" + m.group(1).strip() + "}"
    return v


# ---------------------------------------------------------------------------
# Per-response decisions: input type, answer test, syntax hints
# ---------------------------------------------------------------------------


@dataclass
class _StackInputSpec:
    name: str               # "ans1", "ans2", ...
    type_: str              # "algebraic" | "string" | "numerical"
    tans_var: str           # name of the Maxima variable holding the teacher answer
    tans_expr: str          # actual Maxima literal we'll assign to that variable
    boxsize: int
    forbidfloat: int        # 1 if exact answer expected, 0 otherwise
    syntaxhint: str
    answertest: str         # "AlgEquiv" | "EqualComAss" | "NumAbsolute"


def _input_spec_for(
    idx: int,
    interaction: Interaction,
    rdecl: ResponseDeclaration | None,
    teacher_answer: str | None,
) -> _StackInputSpec:
    name = f"ans{idx}"
    tans_var = f"tans_{name}"
    tans_expr = _onyx_value_to_maxima(teacher_answer or "")

    # Default to algebraic; if the teacher answer is a pure number AND the
    # response declaration is float, use NumAbsolute and forbid float-rounding
    # surprises.
    is_pure_number = bool(re.fullmatch(r"-?\d+(\.\d+)?", tans_expr))
    answertest = "AlgEquiv"
    forbidfloat = 1
    if is_pure_number and rdecl and rdecl.base_type == "float":
        answertest = "NumAbsolute"
        forbidfloat = 0
    # Sets / lists pass through with AlgEquiv — STACK handles `{...}` and `[...]` equality.

    boxsize = max(8, (interaction.expected_length or 15))
    syntaxhint = interaction.placeholder or ""

    return _StackInputSpec(
        name=name,
        type_="algebraic",
        tans_var=tans_var,
        tans_expr=tans_expr,
        boxsize=boxsize,
        forbidfloat=forbidfloat,
        syntaxhint=syntaxhint,
        answertest=answertest,
    )


# ---------------------------------------------------------------------------
# Block builders
# ---------------------------------------------------------------------------


def _render_input(spec: _StackInputSpec) -> str:
    tpl = load_template("input.xml")
    out = substitute_escaped(tpl, "INPUT_NAME", spec.name)
    out = substitute_escaped(out, "INPUT_TYPE", spec.type_)
    out = substitute_escaped(out, "INPUT_TANS", spec.tans_var)
    out = substitute_escaped(out, "INPUT_BOXSIZE", str(spec.boxsize))
    out = substitute_escaped(out, "INPUT_SYNTAXHINT", spec.syntaxhint)
    out = substitute_escaped(out, "INPUT_SYNTAXATTR", "1" if spec.syntaxhint else "0")
    out = substitute_escaped(out, "INPUT_FORBIDFLOAT", str(spec.forbidfloat))
    out = substitute_escaped(out, "INPUT_REQUIRELOWESTTERMS", "0")
    out = substitute_escaped(out, "INPUT_CHECKANSWERTYPE", "0")
    return out


def _render_single_node_prt(spec: _StackInputSpec, max_score: float, true_html: str, false_html: str) -> str:
    """One-node PRT: AlgEquiv(ans, tans). True branch = full credit + correct
    feedback. False branch = 0 score, default penalty (empty <falsepenalty/>)
    and incorrect feedback. Both are leaves (`nextnode == -1`).
    """
    node_tpl = load_template("prt_node.xml")
    wrap_tpl = load_template("prt_wrapper.xml")

    node = substitute_escaped(node_tpl, "NODE_NAME", "0")
    node = substitute_escaped(node, "NODE_DESCRIPTION", "")
    node = substitute_escaped(node, "NODE_ANSWERTEST", spec.answertest)
    node = substitute_escaped(node, "NODE_SANS", spec.name)
    node = substitute_escaped(node, "NODE_TANS", spec.tans_var)
    node = substitute_escaped(node, "NODE_TESTOPTIONS", "")
    node = substitute_escaped(node, "NODE_QUIET", "0")
    node = substitute_escaped(node, "NODE_TRUE_SCORE", "1")
    node = substitute_raw(node, "NODE_TRUE_PENALTY", "<truepenalty>0.0000000</truepenalty>")
    node = substitute_escaped(node, "NODE_TRUE_NEXTNODE", "-1")
    node = substitute_escaped(node, "NODE_TRUE_ANSWERNOTE", f"prt_{spec.name}-1-T")
    node = substitute_cdata(node, "NODE_TRUE_FEEDBACK", true_html)
    node = substitute_escaped(node, "NODE_FALSE_SCORE", "0")
    node = substitute_raw(node, "NODE_FALSE_PENALTY", "<falsepenalty/>")
    node = substitute_escaped(node, "NODE_FALSE_NEXTNODE", "-1")
    node = substitute_escaped(node, "NODE_FALSE_ANSWERNOTE", f"prt_{spec.name}-1-F")
    node = substitute_cdata(node, "NODE_FALSE_FEEDBACK", false_html)

    wrap = substitute_escaped(wrap_tpl, "PRT_NAME", f"prt_{spec.name}")
    wrap = substitute_escaped(wrap, "PRT_VALUE", f"{max_score:.7f}")
    wrap = substitute_escaped(wrap, "PRT_AUTOSIMPLIFY", "1")
    wrap = substitute_escaped(wrap, "PRT_FEEDBACKVARS", "")
    wrap = substitute_raw(wrap, "PRT_NODES", node)
    return wrap


def _render_tags(category_path: list[str]) -> str:
    """Emit at least one TE:1:<area> tag from the top of the category path
    (the validator warns if missing). Append the next two levels as
    TE:2 / TE:3 if available."""
    if not category_path:
        return ""
    parts = [p.strip().replace("'", "'") for p in category_path if p.strip()]
    lines: list[str] = []
    if len(parts) >= 1:
        lines.append(f"      <tag><text>TE:1:{parts[0]}</text></tag>")
    if len(parts) >= 2:
        lines.append(f"      <tag><text>TE:2:{parts[1]}</text></tag>")
    if len(parts) >= 3:
        lines.append(f"      <tag><text>TE:3:{parts[2]}</text></tag>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Top-level translator
# ---------------------------------------------------------------------------


def translate(item: AssessmentItem, assets: list = None, category_path: list[str] = None) -> str:
    """Return one `<question type="stack">...</question>` block (with the
    `<quiz>` envelope stripped) ready to be appended to a Moodle XML bundle.
    """
    assets = assets or []
    category_path = category_path or []

    text_entries = [i for i in item.interactions if i.kind == "textEntry"]
    if not text_entries:
        raise ValueError("STACK translator requires at least one textEntryInteraction")

    # Map RESPONSE_x -> teacher answer (from templateBinding if present,
    # else from responseDeclaration.correctResponse).
    tans_by_rid: dict[str, str] = {}
    for tb in item.template_bindings:
        if tb.custom_op == "VARIABLESTRING" and tb.value is not None:
            tans_by_rid[tb.response_identifier] = tb.value
    for rid, rd in item.response_decls.items():
        if rid in tans_by_rid:
            continue
        if rd.correct_values:
            tans_by_rid[rid] = rd.correct_values[0]

    # Build per-input specs
    specs: list[_StackInputSpec] = []
    for idx, ix in enumerate(text_entries, start=1):
        rdecl = item.response_decls.get(ix.response_identifier)
        teacher = tans_by_rid.get(ix.response_identifier)
        specs.append(_input_spec_for(idx, ix, rdecl, teacher))

    # questionvariables: tans_ansN : <maxima_literal>;
    qv_lines = [f"{s.tans_var} : {s.tans_expr};" for s in specs]
    questionvariables = "\n".join(qv_lines)

    # questiontext: replace each textEntry by [[input:ansN]] [[validation:ansN]]
    replacements = {
        ix.response_identifier: f"[[input:{spec.name}]] [[validation:{spec.name}]]"
        for ix, spec in zip(text_entries, specs, strict=True)
    }
    body_html = extract_question_html(item, replacements)
    body_html, image_files = embed_images_as_base64(body_html, assets)

    # Feedback bodies
    correct_fb_html, incorrect_fb_html = _split_feedback(item)
    # general feedback = the worked solution where ONYX provided one
    general_feedback = correct_fb_html or "<p></p>"

    # specificfeedback: concatenate PRT feedback markers
    specific_feedback = "".join(
        f"<p>[[feedback:prt_{s.name}]]</p>" for s in specs
    )

    # PRTs and inputs
    # Each PRT is worth 1.0; defaultgrade is the input count. This avoids
    # float-precision drift in the validator's PRT-sum check (#8) and matches
    # ONYX's default MAXSCORE_RESPONSE_x = 1.0 weighting.
    inputs_block = "\n".join(_render_input(s) for s in specs)
    per_input_score = 1.0
    total_score = float(len(specs))
    prts_block = "\n".join(
        _render_single_node_prt(
            s,
            per_input_score,
            true_html="<p>Richtig.</p>",
            false_html=incorrect_fb_html or "<p>Noch nicht richtig.</p>",
        )
        for s in specs
    )

    # questionnote: show teacher answers (Moodle preview helper)
    qnote = "; ".join(f"{s.name} = {{@{s.tans_var}@}}" for s in specs)

    # Assemble against the shell
    shell = load_template("question_shell.xml")
    out = substitute_escaped(shell, "QUESTION_NAME", item.title or "Untitled")
    body_with_files = body_html + ("\n" + "\n".join(image_files) if image_files else "")
    out = substitute_cdata(out, "QUESTION_HTML", body_with_files)
    out = substitute_cdata(out, "GENERAL_FEEDBACK", general_feedback)
    out = substitute_escaped(out, "DEFAULT_GRADE", f"{total_score:.7f}")
    out = substitute_escaped(out, "STACK_VERSION", STACK_VERSION)
    # questionvariables is element content -> XML-escape (no CDATA in shell)
    out = substitute_escaped(out, "QUESTION_VARIABLES", questionvariables)
    out = substitute_cdata(out, "SPECIFIC_FEEDBACK", specific_feedback)
    out = substitute_escaped(out, "QUESTION_NOTE", qnote)
    out = substitute_escaped(out, "QUESTION_SIMPLIFY", "1")
    out = substitute_escaped(out, "ASSUME_POSITIVE", "0")
    out = substitute_escaped(out, "ASSUME_REAL", "1")
    out = substitute_cdata(out, "PRT_CORRECT_HTML", "<p>Richtig — gut gemacht!</p>")
    out = substitute_cdata(out, "PRT_PARTIALLY_CORRECT_HTML", "<p>Teilweise richtig.</p>")
    out = substitute_cdata(out, "PRT_INCORRECT_HTML", "<p>Noch nicht richtig.</p>")
    out = substitute_raw(out, "INPUTS_BLOCK", inputs_block)
    out = substitute_raw(out, "PRT_WRAPPERS_BLOCK", prts_block)
    out = substitute_raw(out, "QTESTS_BLOCK", "")
    out = substitute_raw(out, "TAGS_BLOCK", _render_tags(category_path))

    # Strip the surrounding <?xml..?><quiz>...</quiz> envelope — we'll add
    # our own envelope when bundling many questions into one file.
    out = re.sub(r"^<\?xml[^?]*\?>\s*", "", out)
    out = out.replace("<quiz>", "", 1)
    out = re.sub(r"</quiz>\s*$", "", out)
    return out.strip()


def _split_feedback(item: AssessmentItem) -> tuple[str, str]:
    """Heuristic: split ONYX modal feedback into (correct, incorrect).

    Most ONYX items emit two feedback blocks gated on FEEDBACKBASIC =
    correct / incorrect plus zero-or-more per-wrong-answer hints. We
    keep the first non-empty 'correct'-style and 'incorrect'-style hits.
    """
    correct_html = ""
    incorrect_html = ""
    for fb in item.feedback:
        title_lower = (fb.title or "").lower()
        looks_correct = (
            "richtig" in title_lower or "korrekt" in title_lower
            or "sehr gut" in title_lower or "perfekt" in title_lower
        )
        looks_incorrect = (
            "leider" in title_lower or "falsch" in title_lower
            or "stimmt noch nicht" in title_lower or "nicht richtig" in title_lower
            or "tipp" in title_lower or "hint" in title_lower
        )
        if looks_correct and not correct_html:
            correct_html = fb.html
        elif looks_incorrect and not incorrect_html:
            incorrect_html = fb.html
        elif not correct_html and not incorrect_html:
            # If we can't tell, use the first feedback as general
            correct_html = fb.html
    return correct_html, incorrect_html
