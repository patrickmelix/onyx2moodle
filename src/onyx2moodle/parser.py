"""Parse ONYX QTI 2.1 assessmentItem XML into a domain model.

We use lxml because ONYX intermixes the QTI default namespace with xhtml in
the itemBody, and we need namespace-aware XPath. ElementTree's namespace
handling is too clumsy for this.

The model is intentionally lossy where lossy is correct: we drop QTI-internal
scoring boilerplate (FEEDBACKBASIC, MAXSCORE bookkeeping) and keep only what
a Moodle target actually needs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

QTI_NS = "http://www.imsglobal.org/xsd/imsqti_v2p1"
XHTML_NS = "http://www.w3.org/1999/xhtml"
MATHML_NS = "http://www.w3.org/1998/Math/MathML"

NS = {"q": QTI_NS, "x": XHTML_NS, "m": MATHML_NS}


@dataclass
class ResponseDeclaration:
    identifier: str
    cardinality: str          # "single" | "multiple" | "ordered"
    base_type: str            # "string" | "identifier" | "float" | ...
    correct_values: list[str] = field(default_factory=list)
    mapping: dict[str, float] = field(default_factory=dict)


@dataclass
class TemplateBinding:
    """A `<setCorrectResponse>` inside `<templateProcessing>` — ONYX puts the
    randomised teacher answer here using `customOperator definition='VARIABLESTRING'`."""
    response_identifier: str
    custom_op: str | None      # "VARIABLESTRING" | "MAXIMA" | ...
    value: str | None          # raw payload, e.g. "set(1,5,7,11)"


@dataclass
class GradingRule:
    """One scoring branch in `<responseProcessing>`.

    For ONYX-Maxima items the dominant shape is:
        if not isNull(RESPONSE_x) AND MAXIMA("is(equal(ev($(1)),ev($(2))));", RESPONSE_x, correct(RESPONSE_x)):
            SCORE_RESPONSE_x := MAXSCORE_RESPONSE_x
    """
    response_identifier: str
    custom_op: str | None       # the customOperator definition, if any (e.g. "MAXIMA")
    custom_value: str | None    # the value="..." payload of that operator
    max_score: float = 1.0


@dataclass
class ModalFeedback:
    identifier: str
    title: str
    html: str
    # Optional condition that triggered it (FEEDBACKBASIC = "correct" / "incorrect",
    # or a specific RESPONSE_x identifier match)
    trigger: str = ""


@dataclass
class Choice:
    """One option in a `choiceInteraction` or `inlineChoiceInteraction`."""
    identifier: str
    html: str


@dataclass
class Interaction:
    """One interactive element in the question body."""
    kind: str                       # "textEntry" | "choice" | "inlineChoice" | "extendedText" | "upload" | "match" | "gapText" | "hottext"
    response_identifier: str
    # textEntry-specific:
    placeholder: str = ""
    css_class: str = ""             # "maxima-formula" etc.
    expected_length: int | None = None
    # choice/inlineChoice/match:
    choices: list[Choice] = field(default_factory=list)
    shuffle: bool = False
    max_choices: int = 0            # 0 = unlimited (choiceInteraction)
    # matchInteraction: the two simpleMatchSet halves, in declaration order.
    # match_sources are the "left" stems, match_targets the "right" options.
    # The correctResponse pairs use the form "<source_id> <target_id>".
    match_sources: list[Choice] = field(default_factory=list)
    match_targets: list[Choice] = field(default_factory=list)


@dataclass
class AssessmentItem:
    identifier: str
    title: str
    adaptive: bool
    body_xml: etree._Element                       # parsed itemBody
    response_decls: dict[str, ResponseDeclaration]
    template_bindings: list[TemplateBinding]
    grading_rules: list[GradingRule]
    feedback: list[ModalFeedback]
    interactions: list[Interaction]
    raw_xml: bytes

    def has_maxima_grading(self) -> bool:
        return any(g.custom_op == "MAXIMA" for g in self.grading_rules)

    def has_template_processing(self) -> bool:
        return bool(self.template_bindings)


# ---------------------------------------------------------------------------
# Parsing primitives
# ---------------------------------------------------------------------------


def _local(tag: str) -> str:
    """Strip namespace from a tag name."""
    return tag.rsplit("}", 1)[-1]


def _text(elem: etree._Element | None) -> str:
    if elem is None:
        return ""
    return "".join(elem.itertext()).strip()


def _serialise_children(elem: etree._Element) -> str:
    """Return the inner XML of `elem` as a UTF-8 string, namespace-stripped.

    Used to capture HTML fragments (itemBody, simpleChoice contents) for
    re-embedding in Moodle XML.
    """
    if elem is None:
        return ""
    parts: list[str] = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        parts.append(
            etree.tostring(child, encoding="unicode", with_tail=True)
        )
    return _strip_namespaces("".join(parts))


_NS_DECL_RE = None  # lazy-import re below


def _strip_namespaces(xml_fragment: str) -> str:
    """Drop xmlns declarations and ns prefixes from a serialised fragment.

    Moodle's HTML renderer doesn't understand qti/xhtml/mathml prefixes;
    everything has to look like vanilla HTML (with MathML allowed via STACK).
    """
    import re
    global _NS_DECL_RE
    if _NS_DECL_RE is None:
        _NS_DECL_RE = re.compile(r'\s+xmlns(:[a-zA-Z0-9]+)?="[^"]*"')
    s = _NS_DECL_RE.sub("", xml_fragment)
    # Strip "ns:" prefixes on opening and closing tags (but leave attributes
    # like xml:lang alone — those are not from a default ns).
    s = re.sub(r"<([/]?)[a-zA-Z0-9]+:", r"<\1", s)
    return s


# ---------------------------------------------------------------------------
# Component parsers
# ---------------------------------------------------------------------------


def _parse_response_declarations(root: etree._Element) -> dict[str, ResponseDeclaration]:
    out: dict[str, ResponseDeclaration] = {}
    for rd in root.findall("q:responseDeclaration", NS):
        ident = rd.get("identifier") or ""
        cardinality = rd.get("cardinality") or "single"
        base_type = rd.get("baseType") or "string"
        correct = [
            _text(v) for v in rd.findall("q:correctResponse/q:value", NS)
        ]
        mapping: dict[str, float] = {}
        for me in rd.findall("q:mapping/q:mapEntry", NS):
            key = me.get("mapKey") or ""
            try:
                mapping[key] = float(me.get("mappedValue") or 0.0)
            except ValueError:
                pass
        out[ident] = ResponseDeclaration(
            identifier=ident,
            cardinality=cardinality,
            base_type=base_type,
            correct_values=correct,
            mapping=mapping,
        )
    return out


def _parse_template_processing(root: etree._Element) -> list[TemplateBinding]:
    bindings: list[TemplateBinding] = []
    tp = root.find("q:templateProcessing", NS)
    if tp is None:
        return bindings
    for scr in tp.findall(".//q:setCorrectResponse", NS):
        rid = scr.get("identifier") or ""
        co = scr.find("q:customOperator", NS)
        if co is not None:
            bindings.append(
                TemplateBinding(
                    response_identifier=rid,
                    custom_op=co.get("definition"),
                    value=co.get("value"),
                )
            )
        else:
            v = scr.find("q:baseValue", NS)
            bindings.append(
                TemplateBinding(
                    response_identifier=rid,
                    custom_op=None,
                    value=_text(v),
                )
            )
    return bindings


def _parse_response_processing(root: etree._Element) -> list[GradingRule]:
    """Pick out the per-response scoring branches.

    ONYX response processing is verbose but stereotyped. The shape we
    actually care about per response is the `customOperator` (if any)
    inside the responseIf that gates `setOutcomeValue SCORE_RESPONSE_x`.
    Everything else (FEEDBACKBASIC, clamps) is QTI bookkeeping.
    """
    rules: list[GradingRule] = []
    rp = root.find("q:responseProcessing", NS)
    if rp is None:
        return rules

    # Map response_id -> MAXSCORE float, pulled from outcomeDeclaration
    maxscore_for: dict[str, float] = {}
    for od in root.findall("q:outcomeDeclaration", NS):
        ident = od.get("identifier") or ""
        if ident.startswith("MAXSCORE_RESPONSE_"):
            rid = ident[len("MAXSCORE_") :]
            v = od.find("q:defaultValue/q:value", NS)
            try:
                maxscore_for[rid] = float(_text(v))
            except (TypeError, ValueError):
                maxscore_for[rid] = 1.0

    for cond in rp.findall("q:responseCondition", NS):
        rif = cond.find("q:responseIf", NS)
        if rif is None:
            continue
        # The setOutcomeValue inside gives us which response this branch scores
        sov = rif.find("q:setOutcomeValue", NS)
        if sov is None:
            continue
        score_ident = sov.get("identifier") or ""
        if not score_ident.startswith("SCORE_RESPONSE_"):
            continue
        rid = score_ident[len("SCORE_") :]
        co = rif.find(".//q:customOperator", NS)
        custom_op = co.get("definition") if co is not None else None
        custom_value = co.get("value") if co is not None else None
        rules.append(
            GradingRule(
                response_identifier=rid,
                custom_op=custom_op,
                custom_value=custom_value,
                max_score=maxscore_for.get(rid, 1.0),
            )
        )
    return rules


def _parse_modal_feedback(root: etree._Element) -> list[ModalFeedback]:
    out: list[ModalFeedback] = []
    for fb in root.findall("q:modalFeedback", NS):
        out.append(
            ModalFeedback(
                identifier=fb.get("identifier") or "",
                title=fb.get("title") or "",
                html=_serialise_children(fb),
                trigger="",  # filled in by the translator if it can match it
            )
        )
    return out


def _parse_interactions(root: etree._Element) -> list[Interaction]:
    body = root.find("q:itemBody", NS)
    if body is None:
        return []
    out: list[Interaction] = []
    for elem in body.iter():
        tag = _local(elem.tag)
        if tag == "textEntryInteraction":
            out.append(Interaction(
                kind="textEntry",
                response_identifier=elem.get("responseIdentifier") or "",
                placeholder=elem.get("placeholderText") or "",
                css_class=elem.get("class") or "",
                expected_length=_int_or_none(elem.get("expectedLength")),
            ))
        elif tag == "choiceInteraction":
            choices = [
                Choice(
                    identifier=c.get("identifier") or "",
                    html=_serialise_children(c),
                )
                for c in elem.findall("q:simpleChoice", NS)
            ]
            out.append(Interaction(
                kind="choice",
                response_identifier=elem.get("responseIdentifier") or "",
                choices=choices,
                shuffle=(elem.get("shuffle") == "true"),
                max_choices=int(elem.get("maxChoices") or "0"),
            ))
        elif tag == "inlineChoiceInteraction":
            choices = [
                Choice(
                    identifier=c.get("identifier") or "",
                    html=_serialise_children(c),
                )
                for c in elem.findall("q:inlineChoice", NS)
            ]
            out.append(Interaction(
                kind="inlineChoice",
                response_identifier=elem.get("responseIdentifier") or "",
                choices=choices,
                shuffle=(elem.get("shuffle") == "true"),
            ))
        elif tag == "extendedTextInteraction":
            out.append(Interaction(
                kind="extendedText",
                response_identifier=elem.get("responseIdentifier") or "",
                expected_length=_int_or_none(elem.get("expectedLength")),
            ))
        elif tag == "uploadInteraction":
            out.append(Interaction(
                kind="upload",
                response_identifier=elem.get("responseIdentifier") or "",
            ))
        elif tag == "matchInteraction":
            sets = elem.findall("q:simpleMatchSet", NS)
            sources = sets[0] if len(sets) >= 1 else None
            targets = sets[1] if len(sets) >= 2 else None
            def _ac(s):
                return [
                    Choice(identifier=c.get("identifier") or "",
                           html=_serialise_children(c))
                    for c in (s.findall("q:simpleAssociableChoice", NS) if s is not None else [])
                ]
            out.append(Interaction(
                kind="match",
                response_identifier=elem.get("responseIdentifier") or "",
                shuffle=(elem.get("shuffle") == "true"),
                max_choices=int(elem.get("maxAssociations") or "0"),
                match_sources=_ac(sources),
                match_targets=_ac(targets),
            ))
        elif tag == "hottextInteraction":
            out.append(Interaction(
                kind="hottext",
                response_identifier=elem.get("responseIdentifier") or "",
            ))
    return out


def _int_or_none(s: str | None) -> int | None:
    if s is None:
        return None
    try:
        return int(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Top-level parse
# ---------------------------------------------------------------------------


def parse_item(xml_path: Path) -> AssessmentItem:
    raw = Path(xml_path).read_bytes()
    root = etree.fromstring(raw)
    if _local(root.tag) != "assessmentItem":
        raise ValueError(f"Expected <assessmentItem>, got <{_local(root.tag)}>")

    body = root.find("q:itemBody", NS)
    if body is None:
        raise ValueError("Missing <itemBody>")

    return AssessmentItem(
        identifier=root.get("identifier") or "",
        title=root.get("title") or "",
        adaptive=(root.get("adaptive") == "true"),
        body_xml=body,
        response_decls=_parse_response_declarations(root),
        template_bindings=_parse_template_processing(root),
        grading_rules=_parse_response_processing(root),
        feedback=_parse_modal_feedback(root),
        interactions=_parse_interactions(root),
        raw_xml=raw,
    )
