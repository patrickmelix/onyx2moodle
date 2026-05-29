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


def classify(item: AssessmentItem) -> Classification:
    kinds = [i.kind for i in item.interactions]
    has_maxima = item.has_maxima_grading()
    has_template = item.has_template_processing()

    # Check for MAXIMAGRAPHIC anywhere in grading
    has_graphic = any(g.custom_op == "MAXIMAGRAPHIC" for g in item.grading_rules)
    if has_graphic:
        return Classification("manual", "MAXIMAGRAPHIC grading not supported", "high", False)

    # Variant randomisation marker: <printedVariable> in body OR templateBindings
    # whose VARIABLESTRING value uses an indexed `$(N)` reference (which means
    # the teacher answer is a child <variable> bound by templateProcessing —
    # i.e. there's non-trivial Maxima script we cannot translate mechanically).
    has_printed_variable = bool(etree.fromstring(item.raw_xml).xpath(
        ".//q:printedVariable", namespaces=NS,
    ))
    has_indexed_ref = any(
        b.custom_op == "VARIABLESTRING"
        and (b.value or "").strip().startswith("$(")
        for b in item.template_bindings
    )
    if has_printed_variable or has_indexed_ref:
        return Classification(
            "manual",
            "uses ONYX template variants (printedVariable / $(N) reference) — needs manual rewrite",
            "high", False,
        )

    # All-textEntry questions
    if kinds and all(k == "textEntry" for k in kinds):
        # Distinguish maxima-formula style from plain string
        is_maxima_style = any(
            "maxima" in (i.css_class or "").lower() for i in item.interactions
        ) or has_maxima
        if is_maxima_style:
            # Inspect templateProcessing complexity. We treat *any* template
            # binding using customOperator MAXIMA (instead of VARIABLESTRING)
            # as "complex randomisation" and defer it.
            complex_template = any(
                b.custom_op == "MAXIMA" for b in item.template_bindings
            )
            if complex_template:
                return Classification(
                    "manual",
                    "STACK with Maxima-script template processing (deferred)",
                    "medium", False,
                )
            return Classification(
                "stack",
                f"textEntry x{len(kinds)} with MAXIMA grading"
                + (f" + {len(item.template_bindings)} template bindings" if has_template else ""),
                "high", True,
            )
        else:
            return Classification(
                "shortanswer",
                f"textEntry x{len(kinds)}, plain string mapping",
                "high", True,
            )

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

    if "hottext" in kinds:
        return Classification(
            "manual",
            "hottextInteraction — needs per-item rewrite",
            "low", False,
        )

    # Mixed-interaction items (textEntry + choice + ...) — too custom for mechanical conversion
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
