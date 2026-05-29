# onyx2moodle

[![CI](https://github.com/patrickmelix/onyx2moodle/actions/workflows/ci.yml/badge.svg)](https://github.com/patrickmelix/onyx2moodle/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/onyx2moodle.svg)](https://pypi.org/project/onyx2moodle/)
[![Python](https://img.shields.io/pypi/pyversions/onyx2moodle.svg)](https://pypi.org/project/onyx2moodle/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Convert ONYX QTI 2.1 question exports — as produced by the OPAL learning
platform (BPS Bildungsportal Sachsen, used at many German universities) —
into Moodle XML question banks. Maxima-graded items become
[`qtype_stack`](https://stack-assessment.org/) questions; the rest map to
core Moodle question types.

- [What it does](#what-it-does)
- [Install](#install)
- [Usage](#usage)
- [CLI reference](#cli-reference)
- [Output format](#output-format)
- [Optional structural validation](#optional-structural-validation)
- [What gets converted, what doesn't](#what-gets-converted-what-doesnt)
- [How it works internally](#how-it-works-internally)
- [Project layout](#project-layout)
- [Development](#development)
- [Releasing](#releasing)
- [Contributing](#contributing)
- [License](#license)

## What it does

OPAL exports questions as nested zip archives:

```
algebra.zip
└── Algebra/Gruppentheorie/Gruppenaxiome_3.zip
    ├── imsmanifest.xml
    ├── id<uuid>.xml             ← QTI 2.1 assessmentItem
    └── *.png                    ← optional embedded media
```

`onyx2moodle` unpacks the tree, classifies each item by its QTI
interaction type plus the ONYX-Maxima extensions
(`customOperator definition="MAXIMA"`, `VARIABLESTRING`), and emits one
Moodle XML bundle ready for import. The inner folder structure becomes
Moodle category headers (`$course$/top/Algebra/Gruppentheorie`).

| ONYX item shape                                            | Moodle target              |
|------------------------------------------------------------|----------------------------|
| `textEntryInteraction` + `MAXIMA` grading                  | `qtype_stack` (1-node PRT) |
| `textEntryInteraction` + plain string mapping              | `shortanswer`              |
| `choiceInteraction` / single `inlineChoiceInteraction`     | `multichoice`              |
| Multiple `inlineChoiceInteraction` in one body             | `cloze` (multianswer)      |
| `extendedTextInteraction`                                  | `essay`                    |
| `uploadInteraction`                                        | `essay` with file response |
| `matchInteraction`                                         | `matching`                 |
| `hottextInteraction`                                       | *manual rewrite*           |
| `MAXIMAGRAPHIC` plot grading                               | *manual rewrite*           |
| Items using `<printedVariable>` / `$(N)` template variants | *manual rewrite*           |

Items that can't be mechanically translated are listed in a
`<bundle>.skipped.log` beside the output bundle, with the reason —
convenient for triage and manual re-authoring.

## Install

From PyPI:

```bash
pip install onyx2moodle
```

From a clone (for development):

```bash
git clone https://github.com/patrickmelix/onyx2moodle
cd onyx2moodle
pip install -e ".[dev]"
```

Requires **Python 3.10+**. The only runtime dependency is `lxml`.

## Usage

```bash
# Classify items and print a coverage report (read-only).
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

## CLI reference

```
onyx2moodle <command> [options]
```

### `unpack`

Extract an outer ONYX zip into a flat per-item tree on disk.

```
onyx2moodle unpack <archive.zip> [--work <dir>]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--work` | `./work` | Where to place the unpacked tree. One subdirectory per item. |

Each unpacked item directory contains `item.xml` (the QTI assessment
item), `manifest.xml` (the IMS manifest, if present), an `assets/`
folder for embedded media, and a `_meta.json` recording the source
archive path and category breadcrumb.

### `inventory`

Run the parser + classifier on every item in the archive and print a
coverage report. No XML is written.

```
onyx2moodle inventory <archive.zip> [--work <dir>] [--json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--work` | `./work` | Same as for `unpack`. |
| `--json` | off | Emit raw JSON instead of the text report. |

Sample output:

```
Inventory: algebra.zip  (307 items)
  Convertible automatically: 280 (91%)

  Target distribution:
    essay         191
    stack          29
    matching       26
    shortanswer    18
    multichoice    12
    cloze           6
    manual         24
    error           1

  Manual / deferred items (top 10 reasons):
      23  uses ONYX template variants (printedVariable / $(N) reference) ...
       1  hottextInteraction — needs per-item rewrite
       ...
```

### `convert`

End-to-end pipeline: unpack → parse → classify → translate → emit.

```
onyx2moodle convert <archive.zip> -o <bundle.xml>
                    [--work <dir>]
                    [--course-root <prefix>]
                    [--only <target>]...
                    [--validate]
```

| Flag | Default | Description |
|------|---------|-------------|
| `-o`, `--output` | (required) | Path for the Moodle XML bundle. |
| `--work` | `./work` | Per-archive unpack location. |
| `--course-root` | `$course$/top` | Prefix for Moodle category headers. |
| `--only` | (all) | Restrict to specific targets: `stack`, `multichoice`, `shortanswer`, `essay`, `matching`, `cloze`. Repeatable. |
| `--validate` | off | Run the external structural validator on each STACK question. |

Side-effects:

- Writes `<bundle>.xml` and `<bundle>.skipped.log` (one line per skipped item).

## Output format

The bundle is a single `<quiz>` document. Category headers reproduce the
ONYX folder tree:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<quiz>
  <question type="category">
    <category><text>$course$/top/Algebra/Gruppentheorie</text></category>
    <info format="html"><text></text></info>
    <idnumber></idnumber>
  </question>

  <question type="stack">
    <name><text>Gruppenaxiome 3</text></name>
    ...
  </question>

  <question type="multichoice">
    ...
  </question>
</quiz>
```

For each `qtype_stack` question we emit a one-node PRT with `AlgEquiv`:

```xml
<questionvariables>
  <text>tans_ans1 : {1,3,5,7};</text>
</questionvariables>
<input>
  <name>ans1</name>
  <type>algebraic</type>
  <tans>tans_ans1</tans>
  ...
</input>
<prt>
  <name>prt_ans1</name>
  <node>
    <name>0</name>
    <answertest>AlgEquiv</answertest>
    <sans>ans1</sans>
    <tans>tans_ans1</tans>
    ...
  </node>
</prt>
```

ONYX `set(...)` literals are rewritten to Maxima native sets (`{...}`);
`$$...$$` LaTeX delimiters become `\(...\)` (inline) or `\[...\]` (display
when a `align`/`equation`/`gather`/`multline`/`eqnarray` environment is
detected); embedded `<img>` references are inlined as base64 `<file>`
blocks using Moodle's `@@PLUGINFILE@@` convention.

## Optional structural validation

If you have a STACK structural validator (any script that takes a Moodle
XML file containing a single `<question type="stack">` and exits 0 on
pass, non-zero on fail, printing one `[WARN]` or `[FAIL]` line per
finding), you can wire it in as a post-emission gate:

```bash
export ONYX2MOODLE_VALIDATOR=/path/to/validate.py
onyx2moodle convert algebra.zip -o algebra.moodle.xml --validate
```

Discovery order:

1. `$ONYX2MOODLE_VALIDATOR`
2. `validate.py` on `PATH`

`onyx2moodle` does not bundle a validator — pick one that suits your
target Moodle/STACK version.

## What gets converted, what doesn't

Each STACK question emits a one-node PRT with `AlgEquiv(ans, tans)`. This
is correct grading, but it has no diagnostic-misconception branches and
no `<qtest>` self-tests. For pedagogically rich STACK questions
(multi-branch feedback per named misconception, deployed-variant
testing, custom answer notes), re-author the converted item by hand
after import.

Specifically out of scope:

- Diagnostic PRT branches and `<qtest>` self-tests.
- Randomised question variants (items that use `<printedVariable>` or
  `VARIABLESTRING` `$(N)` references are *not* mechanically translated;
  they are flagged for manual rewrite).
- `MAXIMAGRAPHIC` plot-based grading.
- Mixed-interaction items (e.g. one body combining `textEntry` and
  `choiceInteraction`) — no clean Moodle equivalent.

A representative OPAL course export of ~300 items typically converts
~85–95% automatically; the remainder are flagged for manual review.
Distribution skews heavily towards `essay` (free-text answers), with a
smaller core of STACK and core Moodle types. Run `onyx2moodle inventory`
on your archive to see your own breakdown.

## How it works internally

Pipeline per item:

1. **Unpack** — `unpack.py` opens the outer zip, walks each inner zip
   (one per question), and writes the QTI XML + assets to a per-item
   directory. The directory tree above the inner zip is recorded as the
   `category_path`.
2. **Parse** — `parser.py` builds a small domain model
   (`AssessmentItem`) from the QTI XML using lxml + namespace-aware
   XPath. Captures response declarations, template bindings, grading
   rules, modal feedback, and the list of interactions.
3. **Classify** — `classifier.py` decides the Moodle target. Includes a
   defensive rule that defers any item using `<printedVariable>` or
   `$(N)` template references to manual rewrite — these encode random
   variant logic that can't be mechanically translated to STACK's
   Maxima `questionvariables`.
4. **Translate** — `translate/*.py` modules each produce one
   `<question type="...">...</question>` block for their target. The
   STACK translator uses slot-substitution templates in
   `templates/*.xml`; the other translators emit XML directly.
5. **Emit** — `emitter.py` groups blocks by category path, writes the
   `<question type="category">` headers, and bundles everything into
   one `<quiz>` document.
6. **Validate** (optional) — `qa.py` extracts each
   `<question type="stack">` block, wraps it as a single-question
   document, and runs your external validator script.

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

## Development

```bash
git clone https://github.com/patrickmelix/onyx2moodle
cd onyx2moodle
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                  # run the suite
ruff check src tests    # lint
```

The test suite covers nested-zip unpacking edge cases, QTI parsing
round-trips, the classifier's routing decisions (including the safeguard
that defers template-variant items), and end-to-end translation
well-formedness for each Moodle target.

CI runs the suite on Python 3.10–3.13 on Linux, with single-Python smoke
runs on macOS and Windows. See
[`.github/workflows/ci.yml`](.github/workflows/ci.yml).

## Releasing

Releases are tag-driven, published to PyPI via Trusted Publishing (OIDC)
— no API tokens to rotate. The two release workflows are:

- [`.github/workflows/release-pypi.yml`](.github/workflows/release-pypi.yml)
  — fires when a `v*` tag is pushed, builds sdist + wheel, asserts the
  tag matches `pyproject.toml`'s `version`, and uploads to PyPI.
- [`.github/workflows/release-testpypi.yml`](.github/workflows/release-testpypi.yml)
  — manual `workflow_dispatch`, same build, uploads to TestPyPI for dry runs.

### One-time PyPI setup (project maintainer)

1. **Trusted publisher on PyPI** — log in to
   <https://pypi.org/manage/account/publishing/> and add a *pending
   publisher* with:
   - PyPI project name: `onyx2moodle`
   - Owner: `patrickmelix`
   - Repository: `onyx2moodle`
   - Workflow: `release-pypi.yml`
   - Environment: `pypi`
2. **Trusted publisher on TestPyPI** — same form at
   <https://test.pypi.org/manage/account/publishing/> with workflow
   `release-testpypi.yml` and environment `testpypi`.
3. **GitHub environments** — under **Settings → Environments**, create
   `pypi` and `testpypi`. Optionally add a *required reviewer* to
   `pypi` so each release requires a human click.

### Cutting a release

```bash
# 1. Bump the version
$EDITOR pyproject.toml          # change `version = "..."`
git commit -am "Release v0.2.0"

# 2. Tag and push
git tag v0.2.0
git push origin main --tags
```

The `Release to PyPI` workflow takes it from there.

To dry-run on TestPyPI first, bump the version to a pre-release suffix
(e.g. `0.2.0rc1`) and run `Release to TestPyPI` via the Actions tab,
then install with:

```bash
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ \
            onyx2moodle
```

## Contributing

Issues and pull requests welcome at
<https://github.com/patrickmelix/onyx2moodle/issues>. Please run the
test suite (`pytest`) and the linter (`ruff check src tests`) before
submitting.

## License

[MIT](LICENSE)
