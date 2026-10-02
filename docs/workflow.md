# Scan Refinement Workflow

`scan-refinery` separates deterministic PDF operations from document-specific
review. The CLI records and applies a global affine transform per page. The
`refine-scan-pdf` skill helps choose measurement features, group layouts, handle
outliers, and perform visual quality assurance.

The baseline changes page rotation, translation, and optionally canvas size. It
does not enhance pixels, remove defects, generate or correct OCR, reconstruct
content, or perform nonlinear dewarping.

## Local artifacts

Keep source PDFs, run directories, plans containing source filenames, and output
PDFs in ignored local paths such as `inputs/`, `runs/`, and `outputs/`. Never
overwrite the input. Do not commit scans, rendered previews, real machine paths,
or conversation identifiers.

All page numbers below are one-based physical PDF page indices. They are
independent of printed page labels.

## Inspect

```powershell
uv run scan-refinery inspect inputs/scan.pdf --run-dir runs/scan --dpi 144
```

Inspection writes:

- `inventory.json`, containing source identity and per-page geometry/content
  observations;
- `previews/page-0001.png` and corresponding page previews;
- `contact-0001.jpg` and additional numbered contact sheets as required by the
  document length.

Use the contact sheets to identify repeated layouts, title or blank pages,
sparse and diagram-heavy pages, running headers, and visual outliers. Use page
previews for formula, diacritic, fine-line, and crop-boundary review.

## Plan

```powershell
uv run scan-refinery plan inputs/scan.pdf `
  --output runs/scan/plan.json `
  --method projection `
  --align none `
  --dpi 144 `
  --max-angle 2.0 `
  --min-confidence 0.25
```

The measurement methods are:

- `projection`: estimates global skew from repeated horizontal structure in
  dense text;
- `header-rule`: estimates skew and an anchor from a long printed rule in the
  top part of a page.

The alignment modes are:

- `none`: rotate without adding an alignment translation;
- `center`: align content centers within the document;
- `anchor`: align measured reference anchors within the document.

`--reference-page N` chooses a page within the same input document as the
alignment target. Without it, document medians provide the target. A reference
page does not establish external page correspondence or substitute content.

Measurement confidence is heuristic support in `[0, 1]`, not a probability.
Measurements below `--min-confidence` become `skip` and remain unchanged.
Annotated pages also become `skip` so links and annotation geometry are retained.
Internal-link, bookmark, and resolved named-destination target pages become
`skip` as well, preserving their destination coordinate systems. Unresolved
named destinations are unsupported.
Predicted clipping becomes `review`; an unresolved `review` blocks application.

### Plan contract

A plan is JSON data with `schemaVersion: 1` and these top-level members:

| Member | Meaning |
| --- | --- |
| `source` | `filename`, `sha256`, and `page_count` bound to the input |
| `settings` | `method`, `dpi`, `max_angle`, `min_confidence`, `align`, and `reference_page` |
| `pages` | One entry for every physical page, in page order |

Each page entry contains:

| Member | Meaning |
| --- | --- |
| `page` | One-based physical page index |
| `source_size_pt` | Original visible width and height in PDF points |
| `canvas_pt` | Planned output width and height in PDF points |
| `rotation_deg` | Counterclockwise rotation in the rendered view |
| `translate_x_pt` | Translation after rotation, positive to the right |
| `translate_y_pt` | Translation after rotation, positive downward |
| `status` | `ready`, `skip`, or `review` |
| `content_bbox_pt` | Visible content bounds `[x0, y0, x1, y1]` in points, or `null` |
| `measurement` | Angle, confidence, method, pixel-space content box and anchor, and detector reasons |
| `reasons` | Page-level planning and review reasons |

Rotation is about the original page center; translation follows rotation.
Image-space measurement boxes and anchors use a top-left origin. PDF plan
translations also use the rendered-view convention of positive right and down.

The baseline retains source dimensions unless a human explicitly edits
`canvas_pt`. A reviewer may edit transforms, canvas size, status, and reasons.
A `skip` entry must have zero rotation and translation and preserve the original
canvas. Use `review` while a transform or canvas remains uncertain. Do not use a
larger canvas to expose content outside the source's visible CropBox.

Review the plan alongside the rendered pages. Check repeated layout groups,
sparse pages, diagrams, running-header exceptions, detector outliers, formulas,
accents, punctuation, subscripts, superscripts, and fine strokes. Compare
baselines near the top, middle, and bottom: a global angle cannot correct local
page curl or nonlinear deformation.

## Apply

```powershell
uv run scan-refinery apply inputs/scan.pdf `
  --plan runs/scan/plan.json `
  --output outputs/aligned.pdf
```

Application verifies the source SHA-256, page count, and plan geometry. It
refuses stale inputs, malformed transforms, unresolved `review` pages, invalid
`skip` entries, and existing output paths. For altered geometry it independently
recomputes unfiltered source foreground bounds at 144 DPI; editing or removing
a plan's reported content bounds cannot authorize clipping. This raster bound
is conservative and remains subject to visual review of faint and fine marks.

The operation transforms original PDF content rather than rasterizing it again.
Existing image streams and text move together. Source content is clipped to the
original visible CropBox before transformation so a translation or larger canvas
cannot reveal hidden material. Rotated and CropBox-offset page geometry is
normalized on transformed pages. Skipped pages retain their original page
objects, boxes, rotation, links, and annotations. Transforming an annotated page
is rejected in the baseline because it would detach interactive geometry.
Documents with form fields or non-unit `/UserUnit` are rejected.
Catalog open actions, additional/chained actions, and action types beyond static
GoTo, remote GoTo, and URI links are unsupported in this baseline.
Internal navigation targets retain original geometry until verified destination
remapping exists; manually enabling a target page is rejected.

## Verify

```powershell
uv run scan-refinery verify inputs/scan.pdf outputs/aligned.pdf `
  --plan runs/scan/plan.json `
  --output runs/scan/verification.json
```

Verification checks page count, planned canvas dimensions and rotation,
normalized extracted-text equality, source image-stream fingerprints,
annotation properties and appearance streams, link geometry and targets,
bookmarks, named destinations, transformed word centroids, source/plan hashes,
and rendered content against the
planned affine at 72 DPI. Word centers must agree within 0.75 points. Render
checks allow a two-pixel neighborhood, at most 2 percent unmatched dark
foreground in either direction, and mean gray-level error at most 8 on a
0--255 scale. The canonical JSON plan is hashed, so its fingerprint includes
review decisions while ignoring differences in file indentation.
Annotation/navigation fingerprints ignore xref renumbering and round decoded
floating values to 0.001, accommodating serializer and renderer round-off.
These checks establish specific structural and geometric invariants. They do not establish that OCR is
correct, that mathematical notation was recognized faithfully, or that every
page looks good.

Inspect the output into a separate run directory and compare contact sheets and
page previews with the source. Review all detector outliers and a representative
sample from each layout group. Inspect formulas, diacritics, primes, punctuation,
fine diagram strokes, page edges, and former crop boundaries at useful zoom.
Record numerical verification and visual quality assurance separately.

## External reference PDFs

Inspect an external reference PDF in its own run directory. Use it as evidence
for orientation, margins, repeated anchors, and possible local deformation.
The baseline does not infer external page correspondence. Establish any
relationship visually and encode only justified manual transforms in a reviewed
plan. Reference pixels or text never replace source content, and resampling
cannot restore detail absent from the source scan.
