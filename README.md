# onyx2moodle

Convert ONYX QTI 2.1 question exports — as produced by the OPAL learning
platform (BPS Bildungsportal Sachsen, used at many German universities) —
into Moodle XML question banks. Maxima-graded items become
[`qtype_stack`](https://stack-assessment.org/) questions; the rest map to
core Moodle question types.

## What it does

OPAL exports questions as nested zip archives:

```
algebra.zip
└── Algebra/Gruppentheorie/Gruppenaxiome_3.zip
    ├── imsmanifest.xml
    └── id<uuid>.xml             ← QTI 2.1 assessmentItem
    └── *.png                    ← optional embedded media
```

`onyx2moodle` unpacks the tree, classifies each item by its QTI interaction
type plus the ONYX-Maxima extensions (`customOperator definition="MAXIMA"`,
`VARIABLESTRING`), and emits one Moodle XML bundle ready for import. The
inner folder structure becomes Moodle category headers
(`$course$/top/Algebra/Gruppentheorie`).

| ONYX item shape                                       | Moodle target                |
|-------------------------------------------------------|------------------------------|
| `textEntryInteraction` + `MAXIMA` grading             | `qtype_stack` (1-node PRT)   |
| `textEntryInteraction` + plain string mapping         | `shortanswer`                |
| `choiceInteraction` / single `inlineChoiceInteraction`| `multichoice`                |
| Multiple `inlineChoiceInteraction` in one body        | `cloze` (multianswer)        |
| `extendedTextInteraction`                             | `essay`                      |
| `uploadInteraction`                                   | `essay` with file response   |
| `matchInteraction`                                    | `matching`                   |
| `hottextInteraction`                                  | *manual rewrite*             |
| `MAXIMAGRAPHIC` plot grading                          | *manual rewrite*             |
| Items using `<printedVariable>` / `$(N)` template variants | *manual rewrite*       |

Items that can't be mechanically translated are listed in a `.skipped.log`
beside the output bundle, with the reason — convenient for triage and
manual re-authoring.

## Install

```bash
pip install onyx2moodle
```

Or from a clone for development:

```bash
git clone https://github.com/patrickmelix/onyx2moodle
cd onyx2moodle
pip install -e .[dev]
```

Requires Python 3.10+.

## Usage

```bash
# Inventory: classify items, print a coverage report (read-only).
onyx2moodle inventory algebra.zip

# Convert the whole archive to one importable Moodle XML bundle.
onyx2moodle convert algebra.zip -o algebra.moodle.xml

# Restrict to specific Moodle target(s) (repeatable).
onyx2moodle convert algebra.zip -o stack-only.xml --only stack

# Unpack only — useful for inspecting source items.
onyx2moodle unpack algebra.zip --work ./work
```

Import into Moodle: **Question bank → Import → Moodle XML format**. STACK
questions require the [`qtype_stack`](https://stack-assessment.org/)
plugin on the target Moodle.

## Optional structural validation

If you have a STACK structural validator (any script that takes a Moodle
XML file containing a single `<question type="stack">` and exits 0 on
pass, non-zero on fail), you can wire it in as a post-emission gate:

```bash
export ONYX2MOODLE_VALIDATOR=/path/to/validate.py
onyx2moodle convert algebra.zip -o algebra.moodle.xml --validate
```

`onyx2moodle` does not bundle a validator — pick one that suits your
target Moodle/STACK version.

## What gets converted, what doesn't

Each STACK question emits a one-node PRT with `AlgEquiv(ans, tans)`. This
is correct grading, but it has no diagnostic-misconception branches and no
qtests. For pedagogically rich STACK questions (multi-branch feedback per
named misconception, deployed-variant testing, custom answer notes),
re-author the converted item by hand after import.

Specifically out of scope:

- Diagnostic PRT branches and `<qtest>` self-tests.
- Randomised question variants (items that use `<printedVariable>` or
  `VARIABLESTRING` `$(N)` references are *not* mechanically translated;
  they are flagged for manual rewrite).
- `MAXIMAGRAPHIC` plot-based grading.

## Coverage on a typical archive

A representative OPAL course export of ~300 items typically converts
~85–95% automatically; the remainder are flagged for manual review.
Distribution skews heavily towards `essay` (free-text answers), with a
smaller core of STACK and core Moodle types. Run `onyx2moodle inventory`
on your archive to see your own breakdown.

## Project layout

```
src/onyx2moodle/
├── unpack.py            # Nested-zip extractor
├── parser.py            # QTI 2.1 + ONYX-Maxima -> domain model
├── classifier.py        # Per-item routing decisions
├── translate/
│   ├── common.py        # Math delim, image embed, body extraction
│   ├── stack.py         # qtype_stack with 1-node PRT
│   ├── multichoice.py
│   ├── shortanswer.py
│   ├── essay.py
│   ├── matching.py
│   └── cloze.py
├── render/templates.py  # Slot-substitution helpers
├── templates/           # XML templates used during emission
├── emitter.py           # Category headers + bundle envelope
├── qa.py                # Optional external-validator wrapper
└── cli.py               # argparse front-end
```

## Tests

```bash
pytest
```

The suite covers nested-zip unpacking edge cases, QTI parsing round-trips,
the classifier's routing decisions (including the safeguard that defers
template-variant items), and end-to-end translation well-formedness for
each Moodle target.

## License

[MIT](LICENSE)
