"""Reviewable affine plans, source-preserving application, and verification."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import shutil
import statistics
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pymupdf
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import ContentStream, FloatObject, RectangleObject

from scan_refinery.document import (
    annotation_fingerprint,
    file_digest,
    image_fingerprints,
    json_bytes,
    navigation_fingerprint,
    open_document,
    raster,
    source_identity,
    validate_dpi,
)
from scan_refinery.measure import content_bounds, measure_image

LOGGER = logging.getLogger(__name__)
CONTENT_VALIDATION_DPI = 144
RENDER_MEAN_ERROR_LIMIT = 8.0


def _destination_targets(document: pymupdf.Document) -> set[int]:
    """Pin navigation targets until coordinate-bearing destinations can be remapped."""
    for key in ("OpenAction", "AA"):
        if document.xref_get_key(document.pdf_catalog(), key)[0] != "null":
            raise ValueError(f"catalog /{key} actions are outside the affine baseline")
    outline_entries = document.get_toc(simple=False)
    action_objects = [page.xref for page in document]
    action_objects.extend(xref for page in document for xref, _kind, _name in page.annot_xrefs())
    action_objects.extend(item[3]["xref"] for item in outline_entries if item[3].get("xref"))
    for xref in action_objects:
        for key in ("AA", "A/Next"):
            if document.xref_get_key(xref, key)[0] != "null":
                raise ValueError(
                    "additional or chained PDF actions are outside the affine baseline"
                )
        action_type, action_name = document.xref_get_key(xref, "A/S")
        if action_type != "null" and action_name not in {"/GoTo", "/GoToR", "/URI"}:
            raise ValueError("unsupported PDF action is outside the affine baseline")
    targets = set()
    destinations = [link for page in document for link in page.get_links()]
    destinations.extend(item[3] for item in outline_entries)
    destinations.extend(
        dict(destination, kind=pymupdf.LINK_GOTO)
        for destination in document.resolve_names().values()
    )
    for destination in destinations:
        kind = destination.get("kind")
        if kind == pymupdf.LINK_GOTO:
            target = destination.get("page", -1)
            if type(target) is not int or not 0 <= target < document.page_count:
                raise ValueError("unresolved internal PDF destination is outside the baseline")
            targets.add(target + 1)
        elif kind == pymupdf.LINK_NAMED:
            raise ValueError("unresolved named PDF destinations are outside the baseline")
    return targets


def transform_point(x: float, y: float, entry: dict[str, Any]) -> tuple[float, float]:
    """Transform a point in the source's rendered, top-left coordinate system."""
    width, height = entry["source_size_pt"]
    cosine = math.cos(math.radians(entry["rotation_deg"]))
    sine = math.sin(math.radians(entry["rotation_deg"]))
    dx, dy = x - width / 2, y - height / 2
    return (
        cosine * dx + sine * dy + width / 2 + entry["translate_x_pt"],
        -sine * dx + cosine * dy + height / 2 + entry["translate_y_pt"],
    )


def _clipping(entry: dict[str, Any]) -> bool:
    bbox = entry["content_bbox_pt"]
    if bbox is None:
        return False
    x0, y0, x1, y1 = bbox
    width, height = entry["canvas_pt"]
    points = [transform_point(x, y, entry) for x in (x0, x1) for y in (y0, y1)]
    return any(x < -0.1 or y < -0.1 or x > width + 0.1 or y > height + 0.1 for x, y in points)


def make_plan(
    source: Path,
    *,
    method: str = "projection",
    dpi: int = 144,
    max_angle: float = 2.0,
    min_confidence: float = 0.25,
    align: str = "none",
    reference_page: int | None = None,
) -> dict[str, Any]:
    validate_dpi(dpi)
    if not math.isfinite(max_angle) or not 0 < max_angle <= 5:
        raise ValueError("max-angle must be positive and at most 5 degrees")
    if not math.isfinite(min_confidence) or not 0.25 <= min_confidence <= 1:
        raise ValueError("min-confidence must be between 0.25 and 1")
    if align not in {"none", "center", "anchor"}:
        raise ValueError("unknown alignment method")
    if align == "anchor" and method != "header-rule":
        raise ValueError("anchor alignment requires --method header-rule")
    if reference_page is not None and align == "none":
        raise ValueError("reference-page requires --align center or anchor")
    with open_document(source) as document:
        identity = source_identity(source, document)
        destination_targets = _destination_targets(document)
        if reference_page is not None and not 1 <= reference_page <= document.page_count:
            raise ValueError("reference-page is outside the document")
        entries: list[dict[str, Any]] = []
        anchors: dict[int, tuple[float, float]] = {}
        for index, page in enumerate(document):
            LOGGER.info("measuring page %s/%s", index + 1, document.page_count)
            image = raster(page, dpi)
            measurement = measure_image(image, method, max_angle)
            width, height = page.rect.width, page.rect.height
            sx, sy = width / image.shape[1], height / image.shape[0]
            bbox = measurement.content_bbox
            bbox_pt = (
                None if bbox is None else [bbox[0] * sx, bbox[1] * sy, bbox[2] * sx, bbox[3] * sy]
            )
            annotated = bool(page.annot_xrefs())
            pinned_target = index + 1 in destination_targets
            ready = measurement.confidence >= min_confidence and not annotated and not pinned_target
            skip_reason = (
                "internal link or bookmark destination retains source geometry"
                if pinned_target
                else "annotations retained on an unchanged page"
                if annotated
                else "insufficient evidence; source geometry retained"
            )
            entry: dict[str, Any] = {
                "page": index + 1,
                "source_size_pt": [width, height],
                "canvas_pt": [width, height],
                "rotation_deg": measurement.angle_deg if ready else 0.0,
                "translate_x_pt": 0.0,
                "translate_y_pt": 0.0,
                "status": "ready" if ready else "skip",
                "content_bbox_pt": bbox_pt,
                "measurement": asdict(measurement),
                "reasons": [] if ready else [skip_reason],
            }
            if ready and bbox_pt is not None:
                if align == "anchor" and measurement.anchor is not None:
                    point = (measurement.anchor[0] * sx, measurement.anchor[1] * sy)
                else:
                    point = ((bbox_pt[0] + bbox_pt[2]) / 2, (bbox_pt[1] + bbox_pt[3]) / 2)
                anchors[index + 1] = transform_point(*point, entry)
            entries.append(entry)
        if align != "none":
            if reference_page is not None:
                if reference_page not in anchors:
                    raise ValueError("reference page has insufficient alignment evidence")
                target = anchors[reference_page]
            elif anchors:
                target = (
                    statistics.median(point[0] for point in anchors.values()),
                    statistics.median(point[1] for point in anchors.values()),
                )
            else:
                raise ValueError("no supported pages for alignment")
            for entry in entries:
                if entry["page"] in anchors:
                    x, y = anchors[entry["page"]]
                    entry["translate_x_pt"], entry["translate_y_pt"] = target[0] - x, target[1] - y
        for entry in entries:
            if entry["status"] == "ready" and _clipping(entry):
                entry["status"] = "review"
                entry["reasons"].append("proposed transform may clip dark content; revise or skip")
        if file_digest(source) != identity["sha256"]:
            raise ValueError("source changed while measuring; create a new plan")
        return {
            "schemaVersion": 1,
            "source": identity,
            "settings": {
                "method": method,
                "dpi": dpi,
                "max_angle": max_angle,
                "min_confidence": min_confidence,
                "align": align,
                "reference_page": reference_page,
            },
            "pages": entries,
        }


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _vector(value: Any, length: int, name: str) -> list[float]:
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"{name} must contain {length} numbers")
    return [_number(item, name) for item in value]


def load_plan(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        plan = json.load(stream)
    if not isinstance(plan, dict):
        raise ValueError("plan must be a JSON object")
    return plan


def validate_plan(plan: dict[str, Any], source: Path) -> None:
    if (
        not isinstance(plan, dict)
        or type(plan.get("schemaVersion")) is not int
        or plan["schemaVersion"] != 1
    ):
        raise ValueError("unsupported plan schema")
    identity = plan.get("source")
    if not isinstance(identity, dict) or identity.get("sha256") != file_digest(source):
        raise ValueError("plan source hash differs from input PDF")
    entries = plan.get("pages")
    with open_document(source) as document:
        destination_targets = _destination_targets(document)
        if (
            type(identity.get("page_count")) is not int
            or identity["page_count"] != document.page_count
        ):
            raise ValueError("plan source page count differs")
        if not isinstance(entries, list) or len(entries) != document.page_count:
            raise ValueError("plan must contain every source page exactly once")
        for number, (entry, page) in enumerate(zip(entries, document, strict=True), 1):
            if (
                not isinstance(entry, dict)
                or type(entry.get("page")) is not int
                or entry["page"] != number
            ):
                raise ValueError("plan pages must be unique, complete, and in source order")
            size = _vector(entry.get("source_size_pt"), 2, "source_size_pt")
            canvas = _vector(entry.get("canvas_pt"), 2, "canvas_pt")
            if any(
                abs(a - b) > 0.01
                for a, b in zip(size, (page.rect.width, page.rect.height), strict=True)
            ):
                raise ValueError(f"page {number}: source geometry differs")
            if any(value <= 0 or value > 14400 for value in canvas):
                raise ValueError("canvas dimensions must be positive and at most 14400 points")
            angle = _number(entry.get("rotation_deg"), "rotation_deg")
            if abs(angle) > 5:
                raise ValueError("baseline rotation is limited to 5 degrees")
            tx = _number(entry.get("translate_x_pt"), "translate_x_pt")
            ty = _number(entry.get("translate_y_pt"), "translate_y_pt")
            bbox = entry.get("content_bbox_pt")
            if bbox is not None:
                x0, y0, x1, y1 = _vector(bbox, 4, "content_bbox_pt")
                if not (0 <= x0 < x1 <= size[0] and 0 <= y0 < y1 <= size[1]):
                    raise ValueError("content bounds must be within the source page")
            status = entry.get("status")
            if status not in {"ready", "skip", "review"}:
                raise ValueError("page status must be ready, skip, or review")
            if status == "review":
                raise ValueError(f"page {number}: unresolved review decision")
            if page.annot_xrefs() and status != "skip":
                raise ValueError(f"page {number}: annotated pages must be skipped")
            if number in destination_targets and status != "skip":
                raise ValueError(f"page {number}: internal destination target must be skipped")
            if status == "skip" and (angle != 0 or tx != 0 or ty != 0 or canvas != size):
                raise ValueError("skipped pages must retain original geometry")
            if status == "ready" and _clipping(entry):
                raise ValueError(f"page {number}: transform clips measured content")
            if status == "ready" and (angle != 0 or tx != 0 or ty != 0 or canvas != size):
                # The editable plan's bounds are evidence, never authority to
                # discard source content. Recompute from the hash-bound PDF.
                image = raster(page, CONTENT_VALIDATION_DPI)
                actual_bounds = content_bounds(image)
                if actual_bounds is not None:
                    sx, sy = size[0] / image.shape[1], size[1] / image.shape[0]
                    authoritative = dict(entry)
                    authoritative["content_bbox_pt"] = [
                        actual_bounds[0] * sx,
                        actual_bounds[1] * sy,
                        actual_bounds[2] * sx,
                        actual_bounds[3] * sy,
                    ]
                    if _clipping(authoritative):
                        raise ValueError(f"page {number}: transform clips source foreground")


def _check_supported(reader: PdfReader, plan: dict[str, Any]) -> None:
    if reader.is_encrypted:
        raise ValueError("encrypted PDFs are outside the affine baseline")
    if reader.get_fields():
        raise ValueError("form fields are outside the affine baseline")
    for page, entry in zip(reader.pages, plan["pages"], strict=True):
        annotations = page.get("/Annots")
        if annotations is not None and annotations.get_object() and entry["status"] != "skip":
            raise ValueError("annotated pages must be skipped or use an annotation-aware workflow")
        if float(page.get("/UserUnit", 1)) != 1:
            raise ValueError("nonstandard /UserUnit geometry is outside the affine baseline")


def _apply_page(page: Any, entry: dict[str, Any], writer: PdfWriter) -> None:
    page.transfer_rotation_to_content()
    crop = page.cropbox
    left, bottom = float(crop.left), float(crop.bottom)
    width, height = float(crop.width), float(crop.height)
    # Clip before moving content so previously hidden marks cannot be revealed.
    content = page.get_contents()
    if content is not None:
        clipped = ContentStream(None, writer)
        clipped.operations = [
            ([], b"q"),
            (
                [FloatObject(left), FloatObject(bottom), FloatObject(width), FloatObject(height)],
                b"re",
            ),
            ([], b"W"),
            ([], b"n"),
            *content.operations,
            ([], b"Q"),
        ]
        page.replace_contents(clipped)
    angle = entry["rotation_deg"]
    target_width, target_height = entry["canvas_pt"]
    transform = (
        Transformation()
        .translate(-left - width / 2, -bottom - height / 2)
        .rotate(angle)
        .translate(
            width / 2 + entry["translate_x_pt"],
            target_height - height / 2 - entry["translate_y_pt"],
        )
    )
    page.add_transformation(transform)
    for attribute in ("mediabox", "cropbox", "trimbox", "bleedbox", "artbox"):
        setattr(page, attribute, RectangleObject((0, 0, target_width, target_height)))


def _render_check(
    before: pymupdf.Page, after: pymupdf.Page, entry: dict[str, Any]
) -> dict[str, Any]:
    """Check visible content as well as retained, possibly unused resources.

    Rendering at 72 DPI and allowing a two-pixel neighbourhood accommodates
    renderer interpolation. This detects gross omissions and geometry errors;
    it is separate from the exact compressed-image-stream preservation check.
    """
    include_annotations = entry["status"] == "skip"
    original = raster(before, 72, annots=include_annotations)
    refined = raster(after, 72, annots=include_annotations)
    width, height = entry["source_size_pt"]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), entry["rotation_deg"], 1)
    matrix[0, 2] += entry["translate_x_pt"]
    matrix[1, 2] += entry["translate_y_pt"]
    expected = cv2.warpAffine(
        original,
        matrix,
        (refined.shape[1], refined.shape[0]),
        flags=cv2.INTER_LINEAR,
        borderValue=255,
    )
    expected_ink, actual_ink = expected < 180, refined < 180
    kernel = np.ones((5, 5), dtype=np.uint8)
    near_actual = cv2.dilate(actual_ink.astype(np.uint8), kernel) > 0
    near_expected = cv2.dilate(expected_ink.astype(np.uint8), kernel) > 0
    loss = int(np.count_nonzero(expected_ink & ~near_actual)) / max(1, int(expected_ink.sum()))
    gain = int(np.count_nonzero(actual_ink & ~near_expected)) / max(1, int(actual_ink.sum()))
    error = float(np.mean(np.abs(expected.astype(np.int16) - refined.astype(np.int16))))
    return {
        "render_matches_plan": loss <= 0.02 and gain <= 0.02 and error <= RENDER_MEAN_ERROR_LIMIT,
        "render_foreground_loss": loss,
        "render_foreground_gain": gain,
        "render_mean_abs_error": error,
    }


def verify_pdf(source: Path, output: Path, plan: dict[str, Any]) -> dict[str, Any]:
    validate_plan(plan, source)
    return _verify_validated_pdf(source, output, plan)


def _verify_validated_pdf(source: Path, output: Path, plan: dict[str, Any]) -> dict[str, Any]:
    checks = []
    with open_document(source) as original, open_document(output) as refined:
        same_count = original.page_count == refined.page_count
        navigation_matches = navigation_fingerprint(original) == navigation_fingerprint(refined)
        for index, entry in enumerate(plan["pages"]):
            if index >= refined.page_count:
                break
            before, after = original[index], refined[index]
            target_width, target_height = entry["canvas_pt"]
            expected_rotation = before.rotation if entry["status"] == "skip" else 0
            words_before, words_after = before.get_text("words"), after.get_text("words")
            same_words = [word[4] for word in words_before] == [word[4] for word in words_after]
            errors = []
            if same_words:
                for old, new in zip(words_before, words_after, strict=True):
                    point = pymupdf.Point((old[0] + old[2]) / 2, (old[1] + old[3]) / 2)
                    point *= before.rotation_matrix
                    x, y = transform_point(point.x, point.y, entry)
                    observed = pymupdf.Point((new[0] + new[2]) / 2, (new[1] + new[3]) / 2)
                    observed *= after.rotation_matrix
                    errors.append(math.hypot(x - observed.x, y - observed.y))
            max_error = max(errors, default=0.0) if same_words else None
            checks.append(
                {
                    "page": index + 1,
                    "canvas_matches": abs(after.rect.width - target_width) < 0.01
                    and abs(after.rect.height - target_height) < 0.01
                    and after.rotation == expected_rotation,
                    "text_preserved": " ".join(before.get_text().split())
                    == " ".join(after.get_text().split()),
                    "images_preserved": image_fingerprints(original, before)
                    == image_fingerprints(refined, after),
                    "annotations_preserved": annotation_fingerprint(before)
                    == annotation_fingerprint(after),
                    "word_positions_follow_plan": same_words
                    and max_error is not None
                    and max_error <= 0.75,
                    "max_word_center_error_pt": max_error,
                    **_render_check(before, after, entry),
                }
            )
    source_digest = file_digest(source)
    source_matches = source_digest == plan["source"]["sha256"]
    passed = (
        source_matches
        and same_count
        and navigation_matches
        and all(
            page[check]
            for page in checks
            for check in (
                "canvas_matches",
                "text_preserved",
                "images_preserved",
                "annotations_preserved",
                "word_positions_follow_plan",
                "render_matches_plan",
            )
        )
    )
    return {
        "schemaVersion": 1,
        "source_sha256": source_digest,
        "source_matches_plan": source_matches,
        "output_sha256": file_digest(output),
        "plan_sha256": hashlib.sha256(json_bytes(plan)).hexdigest(),
        "passed": passed,
        "page_count_matches": same_count,
        "navigation_preserved": navigation_matches,
        "pages": checks,
        "visual_review": (
            "required separately; numerical checks do not certify "
            "local straightness or OCR accuracy"
        ),
    }


def apply_plan(source: Path, output: Path, plan: dict[str, Any]) -> dict[str, Any]:
    validate_plan(plan, source)
    report_path = output.with_suffix(".report.json")
    for path in (output, report_path):
        if path.exists() or path.is_symlink() or path.resolve() == source.resolve():
            raise FileExistsError(f"refusing to overwrite artifact: {path.name}")
    reader = PdfReader(source)
    _check_supported(reader, plan)
    writer = PdfWriter(clone_from=reader)
    for page, entry in zip(writer.pages, plan["pages"], strict=True):
        if entry["status"] != "skip":
            _apply_page(page, entry, writer)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".pdf", dir=output.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    created = []
    try:
        writer.write(temporary_path)
        report = _verify_validated_pdf(source, temporary_path, plan)
        if not report["passed"]:
            failures = [
                f"page {page['page']}: "
                + ", ".join(key for key, value in page.items() if value is False)
                for page in report["pages"]
                if any(value is False for value in page.values())
            ]
            raise ValueError(
                "output failed preservation checks; no final PDF written; "
                + "; ".join(failures[:8])
            )
        if file_digest(source) != plan["source"]["sha256"]:
            raise ValueError("source changed while applying the plan")
        with output.open("xb") as target, temporary_path.open("rb") as validated:
            created.append(output)
            shutil.copyfileobj(validated, target)
        with report_path.open("xb") as stream:
            created.append(report_path)
            stream.write(json_bytes(report))
        return report
    except Exception:
        for path in created:
            path.unlink()
        raise
    finally:
        writer.close()
        temporary_path.unlink(missing_ok=True)
