# Roadmap

## Shipped 0.1 baseline

The initial release provides a local, reviewable affine workflow:

- inventory JSON, per-page previews, and dynamically paged contact sheets;
- global projection and running-header-rule measurements with explicit heuristic
  confidence and safe unchanged results for weak evidence;
- source-bound JSON plans with per-page rotation, translation, canvas, status,
  content bounds, measurements, and reasons;
- optional within-document center or anchor alignment, including a selected
  reference page;
- transformations applied to original PDF content so existing image streams and
  text move together;
- clipping to the original visible CropBox, normalized rotated and offset page
  geometry on transformed pages, intact skipped pages with annotations, and
  rejection of unsupported annotation transforms, forms, or `/UserUnit` cases;
- structural verification of page geometry, normalized extracted text, image
  stream fingerprints, transformed word positions, source/plan identity, and
  rendered-content agreement with the plan;
- an LLM skill for layout grouping, reference-feature selection, outlier review,
  local-deformation diagnosis, and final visual quality assurance.

This baseline corrects only global affine placement. Numerical verification and
visual review remain separate evidence.

## Candidate upgrades

Later work should be driven by representative local scans and regression
fixtures rather than assumed quality gains.

### Measurement and planning

- Calibrate confidence and clipping prediction against varied typography,
  diagrams, blank pages, and degraded scans.
- Add reviewed document profiles for recurring layouts without hiding per-page
  decisions.
- Explore automatic external-reference correspondence only with explicit
  ambiguity handling and retained visual review. The current workflow uses
  separately inspected references and manual plans.

### Image treatment

- Add optional, reversible tonal enhancement with before/after evidence and no
  claim of recovering information absent from the source.
- Investigate protected cleanup that treats punctuation, accents, primes,
  subscripts, superscripts, dotted rules, formulas, and diagram strokes as
  content. Component size alone is not adequate evidence for deletion.
- Evaluate local or nonlinear dewarping as a distinct pipeline. It cannot reuse
  an existing OCR coordinate map without a verified remap or regenerated text
  layer.

### OCR

- Add opt-in OCR generation for image-only sources behind a separate interface.
- Add OCR correction only with explicit provenance, formula-aware review, and a
  retained distinction between source text, machine output, and human edits.
- Validate reading order, word geometry, language selection, mathematical
  symbols, and diacritics; searchability alone is not an accuracy result.

### PDF feature coverage

- Design preservation semantics for annotations and form fields before
  transforming them. Unchanged annotation pages and internal navigation targets
  are preserved in the baseline; destination remapping remains deferred.
- Evaluate non-unit `/UserUnit`, unusual transparency, nested page resources,
  and other advanced PDF geometry with dedicated fixtures.
- Add broader cross-renderer checks if real failures show that the current
  structural and visual verification is insufficient.

These items are deferred capabilities, not promises that every scan can be
made sharper, complete, searchable, or typographically correct.
