# scan-refinery

Reusable, local tools for aligning scanned PDFs, with a reviewable transform
plan and a companion LLM skill for page-specific judgment.

The first version inspects documents, renders previews, measures global skew,
and applies rotation and translation to the original PDF content. Existing
images and searchable text move together. It does not redraw text, regenerate
OCR, remove specks, sharpen scans, or apply nonlinear dewarping.

## Quick start

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run
these commands from the repository root:

```powershell
uv sync --locked
uv run scan-refinery inspect inputs/scan.pdf --run-dir runs/example
uv run scan-refinery plan inputs/scan.pdf --output runs/example/plan.json
uv run scan-refinery apply inputs/scan.pdf --plan runs/example/plan.json --output outputs/aligned.pdf
uv run scan-refinery verify inputs/scan.pdf outputs/aligned.pdf --plan runs/example/plan.json
```

Put scans in `inputs/` or pass any local input path. Source PDFs, working files,
and outputs are ignored by Git. Original PDFs and existing output files are
never overwritten. Dependencies and Python environments stay local to the
project; no LLM API key or external service is required.

Review the previews and plan before applying it. Measurement confidence is a
heuristic. A sparse or uncertain page is marked `skip` and left unchanged;
unsafe proposed geometry is marked `review` and blocks application until the
plan is corrected or the page is explicitly skipped.
Annotated pages and internal navigation targets stay unchanged; advanced PDF
actions and forms require a separate workflow.

For a document with repeated long running-header rules:

```powershell
uv run scan-refinery plan inputs/scan.pdf --output runs/example/header-plan.json --method header-rule --align anchor
```

Use `--reference-page 5` to choose a page within the same document as the
alignment target. Without a selected page, alignment uses document medians.
See [the workflow guide](docs/workflow.md) for plan editing, reference PDFs,
verification limits, and the boundary between scripts and LLM judgment.

## LLM workflow

Ask an agent to use
[refine-scan-pdf](.agents/skills/refine-scan-pdf/SKILL.md) with the source and
desired outcome. The skill selects useful reference features, examines
outliers and formula-heavy pages, edits the plan where justified, and checks
the final rendering. Mechanical steps use the CLI.

The repository carries the Python profile from
[agent-workbench](https://github.com/KiringYJ/agent-workbench).
Project-specific conventions live in [AI_AGENT_PROJECT.md](AI_AGENT_PROJECT.md).

## Development

```powershell
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
git diff --check
```

The roadmap is in [docs/roadmap.md](docs/roadmap.md). This repository's code is
MIT licensed. Its dependencies retain their own licenses; in particular,
PyMuPDF is available under AGPL or a commercial license.
