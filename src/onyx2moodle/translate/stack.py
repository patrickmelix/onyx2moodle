"""ONYX (textEntryInteraction + MAXIMA grading) -> Moodle qtype_stack.

Two shapes are handled:

1. Static teacher answer (no templateProcessing or trivial VARIABLESTRING
   binding): teacher answer is a Maxima literal pulled straight from
   `responseDeclaration.correctValue` or a baseValue setCorrectResponse.

2. Templated teacher answer (ONYX template-variant questions):
   `<templateProcessing>` defines random integers, random list picks,
   and chained Maxima expressions via `customOperator definition="MAXIMA"`.
   We translate each `<setTemplateValue>` block into a Maxima statement
   in STACK's `<questionvariables>` slot, with the `$(N)` indexed refs
   substituted for the referenced template-variable names. The teacher
   answer is then either a single variable reference (VARIABLESTRING +
   `$(1)`) or a Maxima expression (MAXIMA + value with refs). The
   `<printedVariable identifier="X"/>` placeholders in the body and
   feedback already become `{@X@}` via `extract_question_html`.

`MAXIMAGRAPHIC` template variables (auto-generated PNG plots tied to the
random vars) have no STACK equivalent — those variables are skipped, and
any printedVariable referencing them is replaced in the body with a
visible German placeholder noting that the graphic is unavailable in
this Phase-1 conversion (see `_rewrite_printedvariables`).

Produces a mechanical, *correct* 1-node PRT per response: a single
`AlgEquiv(ans, tans)` test. No diagnostic-misconception branches and no
qtests — those are out of scope for a mechanical converter and should be
added by hand for individual high-value questions after import.

The emitted XML imports cleanly into Moodle's `qtype_stack` plugin and
parses against any external STACK structural validator (see `qa.py`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from lxml import etree

from ..parser import NS, AssessmentItem, Interaction, ResponseDeclaration
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


# ---------------------------------------------------------------------------
# ONYX templateProcessing -> STACK questionvariables
# ---------------------------------------------------------------------------


_DOLLAR_REF = re.compile(r"\$\((\d+)\)")
_RANDOM_CALL = re.compile(r"\brandom\s*\(")
_LOAD_STATEMENT = re.compile(r"\bload\s*\([^)]*\)\s*[,;]?")


def _substitute_indexed_refs(expr: str, refs: list[str]) -> str:
    """In a Maxima value string, replace `$(N)` with the Nth referenced
    variable identifier. ONYX's QTI uses 1-based indices."""

    def repl(m: re.Match) -> str:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(refs):
            return refs[idx]
        return m.group(0)  # leave unsubstituted if out of range

    return _DOLLAR_REF.sub(repl, expr)


def _normalise_onyx_maxima(expr: str) -> str:
    """Rewrite ONYX-Maxima idioms that STACK's sandbox rejects.

    - `random(N)` -> `rand(N)`. ONYX exposes Maxima's `random(...)`; STACK
      forbids the bare name and exposes `rand(...)` instead (range semantics
      are identical for integer N: `[0, N)`).
    - `load("draw")` / `load("...")` -> stripped. STACK pre-loads everything
      it allows via `stackmaxima.mac`; calling `load(...)` errors with
      "Verbotene Funktion: load.". The wrapping `block(load(...), expr)` is
      preserved as-is; only the load call itself is removed.

    These transformations apply to ALL ONYX MAXIMA value strings (template
    assignments and setCorrectResponse expressions alike) so the resulting
    questionvariables block passes STACK's runtime lint.
    """
    expr = _RANDOM_CALL.sub("rand(", expr)
    expr = _LOAD_STATEMENT.sub("", expr)
    return expr


def _template_processing_to_maxima(
    item: AssessmentItem,
) -> tuple[list[str], set[str]]:
    """Walk `<templateProcessing>/<setTemplateValue>` and emit Maxima
    assignments suitable for STACK's `<questionvariables>` block.

    Returns `(maxima_lines, skipped_idents)`:
      - `maxima_lines`: list of `<ident>: <expr>` statements WITHOUT trailing
        semicolons (the caller appends `;`).
      - `skipped_idents`: identifiers whose source operator we couldn't
        translate (MAXIMAGRAPHIC, unknown random shape).

    Empty list + empty set means the item has no templateProcessing at all.
    """
    try:
        root = etree.fromstring(item.raw_xml)
    except etree.XMLSyntaxError:
        return [], set()
    tp = root.find("q:templateProcessing", NS)
    if tp is None:
        return [], set()

    lines: list[str] = []
    skipped: set[str] = set()

    for stv in tp.findall("q:setTemplateValue", NS):
        ident = stv.get("identifier")
        if not ident:
            continue
        children = list(stv)
        if not children:
            continue
        child = children[0]
        tag = etree.QName(child).localname

        if tag == "randomInteger":
            lo = child.get("min", "0")
            hi = child.get("max", "0")
            lines.append(f"{ident}: rand_with_step({lo}, {hi}, 1)")
            continue

        if tag == "randomFloat":
            lo = child.get("min", "0")
            hi = child.get("max", "1")
            # STACK's rand() on a float returns [0, x); approximate uniform [lo, hi].
            lines.append(f"{ident}: float({lo} + rand(1.0)*(({hi})-({lo})))")
            continue

        if tag == "random":
            mult = child.find("q:multiple", NS)
            if mult is None:
                skipped.add(ident)
                continue
            vals = []
            for bv in mult.findall("q:baseValue", NS):
                t = (bv.text or "").strip()
                if t:
                    vals.append(t)
            if not vals:
                skipped.add(ident)
                continue
            lines.append(f"{ident}: rand([{','.join(vals)}])")
            continue

        if tag == "customOperator":
            definition = child.get("definition")
            value = (child.get("value") or "").strip()
            refs = [v.get("identifier") for v in child.findall("q:variable", NS) if v.get("identifier")]
            if definition == "MAXIMAGRAPHIC":
                skipped.add(ident)
                continue
            if definition == "MAXIMA":
                expr = _substitute_indexed_refs(value, refs)
                expr = _normalise_onyx_maxima(expr)
                expr = expr.rstrip(";").strip()
                if expr:
                    lines.append(f"{ident}: {expr}")
                else:
                    skipped.add(ident)
                continue
            # Unknown customOperator definition: skip.
            skipped.add(ident)
            continue

        # Some other QTI element we don't understand.
        skipped.add(ident)

    return lines, skipped


def _teacher_answer_from_template(
    item: AssessmentItem, response_identifier: str
) -> str | None:
    """Return the Maxima expression (or variable name) that the teacher
    answer should evaluate to for response `response_identifier`, derived
    from the corresponding `<setCorrectResponse>` block. Returns None if
    no template-based teacher answer is defined.
    """
    try:
        root = etree.fromstring(item.raw_xml)
    except etree.XMLSyntaxError:
        return None
    tp = root.find("q:templateProcessing", NS)
    if tp is None:
        return None
    for scr in tp.findall("q:setCorrectResponse", NS):
        if scr.get("identifier") != response_identifier:
            continue
        co = scr.find("q:customOperator", NS)
        if co is None:
            bv = scr.find("q:baseValue", NS)
            if bv is not None and bv.text:
                return bv.text.strip()
            return None
        definition = co.get("definition")
        value = (co.get("value") or "").strip()
        refs = [v.get("identifier") for v in co.findall("q:variable", NS) if v.get("identifier")]
        if definition == "VARIABLESTRING":
            # Only claim the template-derived path when the value actually
            # references a template variable. Pure-literal VARIABLESTRING
            # values (e.g. `set(1,3,5,7)` from non-random items) belong to
            # the static path so `_onyx_value_to_maxima` can rewrite ONYX
            # literals into Maxima.
            if not refs and "$(" not in value:
                return None
            if value == "$(1)" and len(refs) == 1:
                return refs[0]
            return _normalise_onyx_maxima(_substitute_indexed_refs(value, refs))
        if definition == "MAXIMA":
            expr = _substitute_indexed_refs(value, refs)
            expr = _normalise_onyx_maxima(expr)
            return expr.rstrip(";").strip()
        return None
    return None


_PRINTED_VARIABLE_TAG = re.compile(
    r'<printedVariable\b[^>]*\bidentifier="([^"]+)"[^>]*/>'
)


def _rewrite_printedvariables(html: str, skipped_idents: set[str]) -> str:
    """Convert any remaining `<printedVariable identifier="X"/>` tags to
    STACK `{@X@}` interpolation, and replace references to skipped (eg
    MAXIMAGRAPHIC) variables with a small placeholder so the rendered
    question still reads correctly.

    `extract_question_html` already does this for the question body, but
    feedback HTML pulled out of `<modalFeedback>` goes through a different
    path — apply the same rule here.
    """
    def repl(m: re.Match) -> str:
        ident = m.group(1)
        if ident in skipped_idents:
            return "<em>[Grafik in dieser Phase-1-Konvertierung nicht verfügbar]</em>"
        return "{@" + ident + "@}"

    html = _PRINTED_VARIABLE_TAG.sub(repl, html)
    # Also catch already-rewritten `{@ident@}` for skipped variables (the body
    # extractor may have run first).
    for ident in skipped_idents:
        html = html.replace(
            "{@" + ident + "@}",
            "<em>[Grafik in dieser Phase-1-Konvertierung nicht verfügbar]</em>",
        )
    return html


def _strip_orphan_printedvariables(html: str, skipped_idents: set[str]) -> str:
    """Back-compat alias for the more general `_rewrite_printedvariables`."""
    return _rewrite_printedvariables(html, skipped_idents)


# ---------------------------------------------------------------------------
# Combinatorics-package helpers (inlined under stk_* prefix)
#
# STACK's `stackmaxima.mac` does NOT load the `combinatorics` package, so
# calls to bare names like `perm_cycles`, `permult`, etc. error at runtime
# with "Verbotene Funktion: ...". STACK also forbids redefining built-ins,
# so we can't just `perm_cycles(P) := ...` either. The defensive fix
# documented in workspace memory is: define helpers under a `stk_` prefix
# and rewrite all call sites in the same question.
#
# Helpers below cover every name listed in qa.py:_COMBINATORICS_BARE_NAMES.
# A helper is only prepended to a question's questionvariables when that
# question actually references the bare name.
# ---------------------------------------------------------------------------


_STK_HELPER_BANNER = (
    "/* Combinatorics-Paket ist in STACK nicht geladen; "
    "Helfer mit stk_-Prefix */"
)


_STK_HELPERS: dict[str, str] = {
    # Random permutation of {1, ..., n}. ONYX exposes `random_perm(n)` from
    # the combinatorics package; Maxima base has `random_permutation(L)` which
    # takes a list. Wrap it.
    "random_perm": (
        "stk_random_perm(n) := random_permutation(makelist(i, i, 1, n))"
    ),
    # Cycle decomposition (1-cycles / fixed points dropped, matching Maxima's
    # standard `perm_cycles` output).
    "perm_cycles": (
        "stk_perm_cycles(P) := block([n : length(P), unvisited, cycles : [], i, j, cyc],\n"
        "  unvisited : setify(makelist(k, k, 1, n)),\n"
        "  for i : 1 thru n do (\n"
        "    if elementp(i, unvisited) then (\n"
        "      j : i, cyc : [],\n"
        "      while elementp(j, unvisited) do (\n"
        "        cyc : endcons(j, cyc),\n"
        "        unvisited : disjoin(j, unvisited),\n"
        "        j : P[j]\n"
        "      ),\n"
        "      if length(cyc) > 1 then cycles : endcons(cyc, cycles)\n"
        "    )\n"
        "  ),\n"
        "  cycles\n"
        ")"
    ),
    # Variadic composition. permult(P, Q, ...) — apply P first, then Q, ...
    # so that (permult(P, Q))[i] = Q[P[i]].
    "permult": (
        "stk_permult([args]) := lreduce("
        "lambda([acc, Q], makelist(Q[acc[i]], i, 1, length(acc))), args)"
    ),
    # Sign of permutation via inversion count.
    "perm_parity": (
        "stk_perm_parity(P) := block([n : length(P), inv : 0, i, j],\n"
        "  for i : 1 thru n - 1 do\n"
        "    for j : i + 1 thru n do\n"
        "      if P[i] > P[j] then inv : inv + 1,\n"
        "  if mod(inv, 2) = 0 then 1 else -1\n"
        ")"
    ),
    # Inverse permutation.
    "inv_perm": (
        "stk_inv_perm(P) := block([n : length(P), result, i],\n"
        "  result : makelist(0, i, 1, n),\n"
        "  for i : 1 thru n do result[P[i]] : i,\n"
        "  result\n"
        ")"
    ),
    # Decompose a permutation into a product of transpositions. Returns a
    # list of two-element lists [a, b]. Empty list for identity.
    "perm_decomp": (
        "stk_perm_decomp(P) := block([n : length(P), Q, result : [], i, j],\n"
        "  Q : copylist(P),\n"
        "  for i : 1 thru n do (\n"
        "    if Q[i] # i then (\n"
        "      j : i + 1,\n"
        "      while j <= n and Q[j] # i do j : j + 1,\n"
        "      if j <= n then (\n"
        "        result : endcons([i, j], result),\n"
        "        [Q[i], Q[j]] : [Q[j], Q[i]]\n"
        "      )\n"
        "    )\n"
        "  ),\n"
        "  result\n"
        ")"
    ),
    # permp(P, n): true iff P is a permutation of {1, ..., n}.
    "permp": (
        "stk_permp(P, n) := is(sort(P) = makelist(i, i, 1, n))"
    ),
}


# Match a bare call `<name>(` not already preceded by `stk_` (or other word
# chars that would form a different identifier).
_BARE_COMBINATORICS = re.compile(
    r"(?<![A-Za-z0-9_])(" + "|".join(_STK_HELPERS.keys()) + r")\s*\("
)


def _rewrite_combinatorics_helpers(maxima_block: str) -> tuple[str, list[str]]:
    """Return (rewritten_block, prepended_defs).

    For each combinatorics function that appears as a bare call in
    `maxima_block`, prefix the call site with `stk_` and emit the
    corresponding helper definition. The caller prepends `prepended_defs`
    to the questionvariables block; an empty list means no rewrite was
    needed.

    Calls already prefixed `stk_<name>(` are NOT matched (the negative
    lookbehind on `[A-Za-z0-9_]` rules them out), so this is idempotent.
    """
    used: list[str] = []

    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name not in used:
            used.append(name)
        return "stk_" + name + "("

    new_block = _BARE_COMBINATORICS.sub(repl, maxima_block)
    if not used:
        return maxima_block, []
    defs = [_STK_HELPER_BANNER]
    for name in used:
        defs.append(_STK_HELPERS[name] + ";")
    return new_block, defs


# ---------------------------------------------------------------------------
# Static-answer helpers (existing behaviour for non-template items)
# ---------------------------------------------------------------------------


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
    from_template: bool = False   # True if tans_expr came from templateProcessing


def _input_spec_for(
    idx: int,
    interaction: Interaction,
    rdecl: ResponseDeclaration | None,
    teacher_answer: str | None,
    *,
    template_teacher_expr: str | None = None,
) -> _StackInputSpec:
    name = f"ans{idx}"
    tans_var = f"tans_{name}"

    # Template-derived teacher answer wins over the static fallback when
    # available. Note: for template-derived expressions we do NOT run
    # `_onyx_value_to_maxima` — that helper rewrites ONYX `set(...)`
    # literals, which don't occur in templateProcessing output.
    if template_teacher_expr is not None:
        tans_expr = template_teacher_expr
        from_template = True
    else:
        tans_expr = _onyx_value_to_maxima(teacher_answer or "")
        from_template = False

    # Default to algebraic; if the teacher answer is a pure number AND the
    # response declaration is float, use NumAbsolute and forbid float-rounding
    # surprises. Template-derived answers stay AlgEquiv even when they happen
    # to evaluate to a number for one variant — the type isn't stable.
    answertest = "AlgEquiv"
    forbidfloat = 1
    if not from_template:
        is_pure_number = bool(re.fullmatch(r"-?\d+(\.\d+)?", tans_expr))
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
        from_template=from_template,
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


_MANUAL_ANSWER_TAG = "needs-manual-answer"


def _render_tags(category_path: list[str], extra_tags: list[str] = None) -> str:
    """Emit at least one TE:1:<area> tag from the top of the category path
    (the validator warns if missing). Append the next two levels as
    TE:2 / TE:3 if available, plus any `extra_tags` (free-form strings)
    such as `needs-manual-answer` for items whose OPAL source has empty
    correctResponse blocks."""
    parts = [_escape_text(p.strip()) for p in (category_path or []) if p.strip()]
    lines: list[str] = []
    if len(parts) >= 1:
        lines.append(f"      <tag><text>TE:1:{parts[0]}</text></tag>")
    if len(parts) >= 2:
        lines.append(f"      <tag><text>TE:2:{parts[1]}</text></tag>")
    if len(parts) >= 3:
        lines.append(f"      <tag><text>TE:3:{parts[2]}</text></tag>")
    for tag in (extra_tags or []):
        lines.append(f"      <tag><text>{_escape_text(tag)}</text></tag>")
    return "\n".join(lines)


def _escape_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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

    # ONYX templateProcessing -> Maxima questionvariables (random integers,
    # list-pick, chained MAXIMA expressions). MAXIMAGRAPHIC entries are
    # skipped and any printedVariable referencing them gets a placeholder
    # in the rendered body.
    template_lines, skipped_idents = _template_processing_to_maxima(item)

    # Map RESPONSE_x -> teacher answer. Three-step priority:
    #   1) templateProcessing/setCorrectResponse (random or computed answer)
    #   2) static templateBinding payload (the `value` of a VARIABLESTRING
    #      setCorrectResponse — same source as #1 for non-template items,
    #      kept for backwards compat with the pre-template parser path)
    #   3) responseDeclaration.correctValues (backfill when neither
    #      template-driven path produced an answer)
    template_tans_by_rid: dict[str, str] = {}
    for ix in text_entries:
        expr = _teacher_answer_from_template(item, ix.response_identifier)
        if expr is not None:
            template_tans_by_rid[ix.response_identifier] = expr

    static_tans_by_rid: dict[str, str] = {}
    for tb in item.template_bindings:
        if tb.custom_op == "VARIABLESTRING" and tb.value is not None:
            static_tans_by_rid[tb.response_identifier] = tb.value
    for rid, rd in item.response_decls.items():
        if rid in static_tans_by_rid:
            continue
        if rd.correct_values:
            static_tans_by_rid[rid] = rd.correct_values[0]

    # Build per-input specs
    specs: list[_StackInputSpec] = []
    for idx, ix in enumerate(text_entries, start=1):
        rdecl = item.response_decls.get(ix.response_identifier)
        template_expr = template_tans_by_rid.get(ix.response_identifier)
        fallback = static_tans_by_rid.get(ix.response_identifier)
        specs.append(
            _input_spec_for(
                idx, ix, rdecl, fallback,
                template_teacher_expr=template_expr,
            )
        )

    # Flag inputs whose teacher answer reduces to Maxima `null` — that
    # happens when the OPAL source has an empty <setCorrectResponse value=""/>
    # block ("manuelle Auswertung" in ONYX: the teacher graded manually).
    # We can't synthesise a correct answer mechanically, but we tag the
    # question so the teacher can filter for it and add the answer after
    # import.
    missing_answer_inputs = [s.name for s in specs if s.tans_expr.strip() == "null"]

    # questionvariables: first the template-variable assignments (random ints,
    # chained MAXIMA), then the teacher-answer assignments referencing them.
    qv_lines: list[str] = [f"{line};" for line in template_lines]
    qv_lines.extend(f"{s.tans_var} : {s.tans_expr};" for s in specs)
    if missing_answer_inputs:
        names = ", ".join(missing_answer_inputs)
        qv_lines.append(
            f"/* TODO manual-answer: OPAL source has empty <setCorrectResponse> "
            f"for {names}; teacher must fill in the tans_* assignment(s) above "
            f"before this question can grade. */"
        )
    questionvariables = "\n".join(qv_lines)

    # If the questionvariables block calls combinatorics-package functions
    # by their bare names (`perm_cycles`, `permult`, ...) — which STACK's
    # sandbox forbids — rewrite call sites to `stk_*` and prepend the
    # helper definitions. No-op when no bare call is present.
    questionvariables, helper_defs = _rewrite_combinatorics_helpers(questionvariables)
    if helper_defs:
        questionvariables = "\n".join(helper_defs) + "\n\n" + questionvariables

    # questiontext: replace each textEntry by [[input:ansN]] [[validation:ansN]]
    replacements = {
        ix.response_identifier: f"[[input:{spec.name}]] [[validation:{spec.name}]]"
        for ix, spec in zip(text_entries, specs, strict=True)
    }
    body_html = extract_question_html(item, replacements)
    body_html = _strip_orphan_printedvariables(body_html, skipped_idents)
    body_html, image_files = embed_images_as_base64(body_html, assets)

    # Feedback bodies
    correct_fb_html, incorrect_fb_html = _split_feedback(item)
    correct_fb_html = _strip_orphan_printedvariables(correct_fb_html, skipped_idents)
    incorrect_fb_html = _strip_orphan_printedvariables(incorrect_fb_html, skipped_idents)
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
    extra_tags = [_MANUAL_ANSWER_TAG] if missing_answer_inputs else []
    out = substitute_raw(out, "TAGS_BLOCK", _render_tags(category_path, extra_tags))

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
