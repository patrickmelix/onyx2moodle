"""Classify an ONYX item into the Moodle question type it should become.

Routing table:

    textEntry + MAXIMA grading       -> stack            (qtype_stack, 1-node PRT)
    textEntry + plain string mapping -> shortanswer      (Moodle core)
    choice (single)                  -> multichoice      (single)
    choice (multiple)                -> multichoice      (multi)
    inlineChoice (one)               -> multichoice
    inlineChoice (many)              -> cloze            (multianswer)
    extendedText                     -> essay
    upload                           -> essay (file response)
    match                            -> matching
    hottext                          -> manual           (per-item rewrite)
    MAXIMAGRAPHIC                    -> manual           (plot-grading)
    templateConstraint retry loops   -> manual           (complex randomisation)

Variant metadata (`<printedVariable>` in the body, `$(N)` template refs)
matters **only for textEntry** items, because the teacher answer itself is
randomised there and a static AlgEquiv template can't capture it. For
non-textEntry items the metadata only affects display labels — the
identifier-based correctResponse remains stable, so the structural
translator handles them fine. This is the lesson from items like
`Permutationen_Anwendung_I` (matchInteraction + printedVariable labels)
which were previously mis-deferred to manual.

Items routed to `"manual"` are flagged in the coverage report so they can
be re-authored by hand. `"unknown"` means we couldn't classify the item;
surface it instead of guessing.
"""
from __future__ import annotations

from dataclasses import dataclass

from lxml import etree

from .parser import NS, AssessmentItem

AUTOMATIC_TARGETS = {
    "stack",
    "shortanswer",
    "multichoice",
    "essay",
    "matching",
    "cloze",
}


@dataclass
class Classification:
    target: str                  # Moodle question type, "manual", or "unknown"
    reason: str                  # human-readable rationale
    confidence: str              # "high" | "medium" | "low"
    convertible: bool     # True if a translator can handle this mechanically


def _has_variant_metadata(item: AssessmentItem) -> bool:
    """True if the item uses `<printedVariable>` in its body OR a
    `VARIABLESTRING` template binding with a `$(N)` indexed reference.

    For textEntry items this means the teacher answer is randomised and we
    can't mechanically translate. For other interaction kinds it only
    randomises display labels — the translator still works.
    """
    has_printed_variable = bool(etree.fromstring(item.raw_xml).xpath(
        ".//q:printedVariable", namespaces=NS,
    ))
    has_indexed_ref = any(
        b.custom_op == "VARIABLESTRING"
        and (b.value or "").strip().startswith("$(")
        for b in item.template_bindings
    )
    return has_printed_variable or has_indexed_ref


def classify(item: AssessmentItem) -> Classification:
    kinds = [i.kind for i in item.interactions]
    has_maxima = item.has_maxima_grading()
    has_template = item.has_template_processing()

    # Check for MAXIMAGRAPHIC anywhere in grading
    has_graphic = any(g.custom_op == "MAXIMAGRAPHIC" for g in item.grading_rules)
    if has_graphic:
        return Classification("manual", "MAXIMAGRAPHIC grading not supported", "high", False)

    # ----- Non-textEntry interactions: route by kind first.
    # Variant metadata is harmless here (only affects display, not correctness).

    # Single-choice multiple-choice
    if kinds == ["choice"]:
        ix = item.interactions[0]
        rdecl = item.response_decls.get(ix.response_identifier)
        is_multi = (rdecl and rdecl.cardinality == "multiple") or (ix.max_choices != 1)
        return Classification(
            "multichoice",
            f"choiceInteraction ({'multi' if is_multi else 'single'}, {len(ix.choices)} options)",
            "high", True,
        )

    # Inline choice(s) — one or more dropdowns. With >1 we'd normally use cloze,
    # but Moodle's multichoice can also represent a single inlineChoice cleanly.
    if kinds and all(k == "inlineChoice" for k in kinds):
        if len(kinds) == 1:
            return Classification(
                "multichoice",
                "single inlineChoice", "high", True,
            )
        return Classification(
            "cloze", f"{len(kinds)} inlineChoices (gap-fill)", "medium", True,
        )

    if kinds == ["extendedText"]:
        return Classification("essay", "extendedTextInteraction", "high", True)

    if kinds == ["upload"]:
        return Classification("essay", "uploadInteraction (file response)", "high", True)

    if kinds == ["match"]:
        return Classification("matching", "matchInteraction", "medium", True)

    # ----- textEntry interactions: variant metadata matters because the
    # teacher answer is randomised, and a static AlgEquiv won't capture it.

    if kinds and all(k == "textEntry" for k in kinds):
        is_maxima_style = any(
            "maxima" in (i.css_class or "").lower() for i in item.interactions
        ) or has_maxima
        if is_maxima_style:
            # ONYX template-variant questions: <templateProcessing> defines
            # random integers / random list picks / chained MAXIMA expressions,
            # and the teacher answer references one or more template vars
            # (VARIABLESTRING `$(1)` or a MAXIMA computation). The stack
            # translator emits these as STACK <questionvariables>.
            #
            # Items whose templateProcessing relies critically on
            # MAXIMAGRAPHIC are still translatable — that variable is skipped
            # and any printedVariable referencing it gets a placeholder.
            if has_template or _has_variant_metadata(item):
                summary_parts = []
                if has_template:
                    summary_parts.append("templateProcessing")
                if item.template_bindings:
                    summary_parts.append(f"{len(item.template_bindings)} bindings")
                return Classification(
                    "stack",
                    f"textEntry x{len(kinds)} with MAXIMA grading + "
                    + ", ".join(summary_parts or ["template variants"]),
                    "high", True,
                )
            return Classification(
                "stack",
                f"textEntry x{len(kinds)} with MAXIMA grading",
                "high", True,
            )
        if _has_variant_metadata(item):
            # Plain-string textEntry with template variants: shortanswer has
            # no randomisation, so still defer. (Could be lifted later: emit
            # a STACK string-input question with rand(["A","B"]).)
            return Classification(
                "manual",
                "uses ONYX template variants on a non-Maxima textEntry "
                "— needs manual rewrite",
                "high", False,
            )
        return Classification(
            "shortanswer",
            f"textEntry x{len(kinds)}, plain string mapping",
            "high", True,
        )

    # ----- Everything else

    if "hottext" in kinds:
        return Classification(
            "manual",
            "hottextInteraction — needs per-item rewrite",
            "low", False,
        )

    if len(set(kinds)) > 1:
        return Classification(
            "manual",
            f"mixed interaction types {sorted(set(kinds))}", "low", False,
        )

    if not kinds:
        return Classification("unknown", "no interactions detected", "low", False)

    return Classification(
        "unknown", f"unhandled interaction kinds {kinds}", "low", False,
    )
