---
name: refine-scan-pdf
description: Refine and align a local scanned PDF with scan-refinery when page grouping, reference-feature selection, transform-plan review, and visual verification require document-specific judgment. Use for global skew and placement correction while preserving existing PDF images and text; do not use it to promise enhancement, cleanup, OCR repair, or nonlinear dewarping.
---

# Refine Scan PDF

Use the repository CLI for deterministic inspection, measurement, affine
application, and verification. Own the judgments that the CLI cannot make:
which layouts belong together, which printed features are trustworthy, which
outliers should remain unchanged, and whether the rendered result is visually
acceptable.

## Workflow

1. Keep the source immutable and choose local ignored paths for the run and
   output. Do not add scans, derived pages, real machine paths, or conversation
   identifiers to repository history.
2. Inventory the source:

   ```powershell
   uv run scan-refinery inspect <input.pdf> --run-dir <run> --dpi 144
   ```

   Read `inventory.json`, all paged contact sheets, and relevant page previews.
   Group repeated layouts and note sparse, diagram-heavy, formula-heavy, title,
   blank, damaged, or exceptional pages. Page numbers are one-based physical PDF
   indices.
3. Choose measurement evidence:
   - Use `projection` for dense horizontal text.
   - Use `header-rule` only when a long printed top-page rule is a genuine,
     repeated reference feature.
   - Use `--align center` for consistent content centering or `--align anchor`
     for a trustworthy repeated anchor. `--reference-page N` selects an
     alignment anchor within this document; it does not pair or replace pages.
   - Treat confidence as heuristic support. Inspect low-confidence pages and
     visually compare detector outliers even when their confidence is high.
4. Generate a reviewable plan, adjusting options to the observed document:

   ```powershell
   uv run scan-refinery plan <input.pdf> --output <plan.json> --method projection --align none --dpi 144 --max-angle 2.0 --min-confidence 0.25
   ```

5. Review the plan and previews before application. Check page groups,
   rotation sign, placement, proposed canvas, and clipping. A positive plan
   rotation is counterclockwise in the rendered view; translation is in PDF
   points, positive right and down, after rotation about the original page
   center. Keep uncertain pages `skip` with identity geometry or `review` while
   investigating. `review` blocks application. Human edits may change a page's
   transform, canvas, reasons, and status, but a `skip` page must retain identity
   transform and original geometry.
6. Diagnose local deformation separately. Compare baselines near the top,
   middle, and bottom. A good global angle does not establish local straightness;
   if one affine transform cannot fit the page, leave it unchanged and record
   the limitation rather than forcing a compromise.
7. Apply only a resolved plan:

   ```powershell
   uv run scan-refinery apply <input.pdf> --plan <plan.json> --output <output.pdf>
   ```

   Application enforces source identity and refuses unresolved review or unsafe
   geometry. Do not overwrite the source or an existing artifact.
8. Run numerical preservation checks:

   ```powershell
   uv run scan-refinery verify <input.pdf> <output.pdf> --plan <plan.json> --output <report.json>
   ```

   Then inspect the output into a separate run directory and compare rendered
   contact sheets and per-page previews. Re-render selected pages at 300 DPI or
   higher when fine-detail judgment depends on primes, accents, or fine strokes.
   Review every outlier and a
   representative sample from each layout group. Examine formulas, accents,
   primes, subscripts, superscripts, punctuation, fine diagram strokes, page
   edges, and former crop boundaries. Report numerical verification and visual
   inspection as separate evidence.

## External References

When the user supplies a reference PDF, inspect it in a separate run directory.
Use it only as evidence for margins, anchors, orientation, or likely local
deformation. There is no automatic external page correspondence in the
baseline. Derive a manual plan only when the page relationship and chosen
features are visually justified. Never substitute reference content or imply
that upsampling restores missing source detail.

## Boundaries

The affine baseline preserves original image streams and existing text while
moving them together. Transformed pages normalize rotated and CropBox-offset
input and clip to the original visible CropBox. Annotated pages are kept intact
with `skip`; transforming their content is rejected until annotation geometry
can move with it. Form fields and non-unit `/UserUnit` geometry are unsupported.
Internal-link and bookmark target pages also remain `skip`, since unchanged
destination objects can still point away from content on a transformed page.
Do not override these skips; unresolved named destinations are unsupported.

Do not add enhancement, speck removal, destructive cleanup, OCR generation or
correction, content reconstruction, or nonlinear warping to this workflow.
Searchability does not prove correct OCR, especially for mathematics. If the
requested quality depends on a deferred stage, preserve the current evidence,
state the limitation, and stop without a quality-recovery claim.
