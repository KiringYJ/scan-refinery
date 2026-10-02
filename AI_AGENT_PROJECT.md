# Project-Specific Agent Context

## Architecture

`scan-refinery` is a reusable local Python CLI for scanned-PDF refinement.
Deterministic code owns inventory, rendering, measurements, affine transforms,
and preservation checks. The `refine-scan-pdf` skill owns reference-feature
selection, page grouping, outlier decisions, and visual review.

The initial implementation preserves source PDF content, including its existing
OCR, through global rotation and translation. Enhancement, destructive cleanup,
OCR generation/correction, and nonlinear dewarping are deferred. Keep these
stages separate because a nonlinear image warp invalidates an existing OCR map.

Use project-local uv. The bootstrap dependency decision is NumPy and headless
OpenCV for measurements, PyMuPDF for rendering and geometry inspection, pypdf for
content-preserving transformations, and Pillow for contact sheets. pytest, Ruff,
and ty are development checks. Add later dependencies only for an evidenced need.

## Build Commands

```powershell
uv sync --locked
uv build
uv run scan-refinery --help
```

## Test Commands

```powershell
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
git diff --check
```

Tests use generated small PDFs and images; never commit third-party scans as
fixtures. For geometry changes, check transformed word positions, raw image
streams, clipping boundaries, rotated/cropped input geometry, and source/output
identity. Re-render a representative output and inspect it visually. Avoid
wording-only tests. Real-document smoke tests stay in ignored run directories.

## Important Files and Directories

- `src/scan_refinery/`: CLI, measurements, PDF IO, plan/apply/verify workflow.
- `tests/`: synthetic image and PDF regression coverage.
- `docs/workflow.md`: public command and plan contracts.
- `docs/roadmap.md`: implemented baseline and deferred processing stages.
- `.agents/skills/refine-scan-pdf/`: project-owned visual refinement skill.
- `inputs/`, `runs/`, `outputs/`, `.local/`: ignored local material.
- `uv.lock`: tracked reproducible Python dependency resolution.

## Domain Terms

- PDF page numbers are one-based physical page indices, not printed page labels.
- A transform plan records source identity, measurements, page decisions, and
  affine transforms. It is evidence and editable data, never executable code.
- Image coordinates use a top-left origin. Plan translation uses PDF points,
  positive right and down. Rotation is counterclockwise in the rendered view.
- Measurement confidence is heuristic support, not a probability or proof.
- Existing OCR text is source data. Searchability does not establish accurate
  formula transcription or a corrected mathematical statement.

## Workspace Configuration

All agent-workbench files are shared repository configuration on the normal
development branch. `.agent-workbench.lock.json` owns upstream provenance;
the project skill is local unmanaged content and must survive workbench sync.
Its Claude discovery copy must match the canonical `.agents/skills/` directory.
Personal settings, caches, scans, derived previews/PDFs, and run reports remain
untracked. Never put actual machine paths or conversation identifiers in public
project records. Track neutral, reusable examples instead.

## Project-Specific Constraints

- Never overwrite a source PDF or existing artifacts. A plan is bound to source
  SHA-256, page count, and geometry. Fail on stale input or malformed transforms.
- Preserve every page and source image stream. Transform existing text with the
  image. Keep page-level decisions explicit; uncertain pages remain unchanged.
- Do not silently clip meaningful dark content or reveal formerly cropped
  content. An unresolved `review` page blocks application.
- Preserve annotated pages unchanged with `skip`; reject a request to transform
  them until an annotation-aware workflow exists. Reject form fields and
  nonstandard `/UserUnit` geometry in the affine baseline.
- Keep internal-link and bookmark target pages unchanged as well. Destinations
  encode target coordinates; object equality alone does not prove navigation
  stays attached to content on a transformed page. Unresolved named destinations
  are unsupported until their semantics can be established.
- Reject catalog open actions and additional/chained or unsupported actions;
  preserving an action object is insufficient to prove its dynamic navigation
  remains attached to transformed content.
- Protect punctuation, accents, primes, subscripts, superscripts, dotted lines,
  and fine diagram strokes in future cleanup stages. Never infer OCR or image
  edits from a statistical component-size threshold alone.
- A reference PDF supplies layout/quality evidence, not replacement source
  content. Do not promise that upsampling restores missing source detail.
- Report numerical checks and visual inspection separately. Do not equate
  a passed global-angle check with local baseline straightness.
- Artifacts and code are English; preserve source text, names, and diacritics.
- Bootstrap authorizes local setup and validation; commits and remote publication
  require their own user instruction.
