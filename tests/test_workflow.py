"""Preservation and failure boundaries using generated, redistributable fixtures."""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path

import numpy as np
import pymupdf
import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NullObject

from scan_refinery.cli import main
from scan_refinery.document import file_digest, inspect_pdf, raster
from scan_refinery.workflow import apply_plan, make_plan, validate_plan, verify_pdf


def make_source(path: Path, count: int = 2, *, ocr: bool = True) -> Path:
    document = pymupdf.open()
    for index in range(count):
        page = document.new_page(width=300, height=400)
        image = Image.new("L", (600, 800), "white")
        draw = ImageDraw.Draw(image)
        for row in range(10):
            draw.text((100, 140 + row * 36), f"Synthetic scan line {row + 1}", fill=0)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        page.insert_image(page.rect, stream=buffer.getvalue())
        if ocr:
            page.insert_text((50, 77), f"Searchable page {index + 1}", render_mode=3)
    document.set_metadata({"title": "Synthetic fixture"})
    document.save(path)
    document.close()
    return path


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return make_source(tmp_path / "scan.pdf")


def ready_plan(source: Path) -> dict:
    plan = make_plan(source, dpi=72)
    for entry in plan["pages"]:
        entry["status"] = "ready"
        entry["rotation_deg"] = 0.0
    return plan


def test_affine_moves_images_and_invisible_text_together(source: Path, tmp_path: Path) -> None:
    original_hash = file_digest(source)
    plan = ready_plan(source)
    for entry in plan["pages"]:
        entry.update(
            rotation_deg=1.2,
            translate_x_pt=12.0,
            translate_y_pt=15.0,
            canvas_pt=[350.0, 450.0],
        )
    output = tmp_path / "aligned.pdf"
    report = apply_plan(source, output, plan)
    assert report["passed"]
    assert all(page["max_word_center_error_pt"] < 0.05 for page in report["pages"])
    assert file_digest(source) == original_hash
    assert json.loads(output.with_suffix(".report.json").read_text())["passed"]
    with pymupdf.open(output) as document:
        assert document.metadata["title"] == "Synthetic fixture"
    assert verify_pdf(source, output, plan)["passed"]


def test_rotated_crop_offset_preserves_visible_render_and_ocr(tmp_path: Path) -> None:
    source = make_source(tmp_path / "rotated.pdf", count=1)
    with pymupdf.open(source) as document:
        page = document[0]
        page.draw_rect(pymupdf.Rect(3, 4, 12, 18), color=(0, 0, 0), fill=(0, 0, 0))
        page.set_cropbox(pymupdf.Rect(20, 30, 280, 360))
        page.set_rotation(90)
        document.save(tmp_path / "cropped.pdf")
    source = tmp_path / "cropped.pdf"
    plan = ready_plan(source)
    output = tmp_path / "normalized.pdf"
    report = apply_plan(source, output, plan)
    assert report["passed"]
    with pymupdf.open(source) as original, pymupdf.open(output) as refined:
        assert refined[0].rotation == 0
        np.testing.assert_array_equal(raster(original[0], 72), raster(refined[0], 72))


def test_uncertain_pages_are_identity_and_dpi_is_bounded(tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    with pymupdf.open() as document:
        document.new_page(width=300, height=400)
        document.save(path)
    plan = make_plan(path, dpi=72)
    assert plan["pages"][0]["status"] == "skip"
    assert plan["pages"][0]["rotation_deg"] == 0
    apply_plan(path, tmp_path / "blank-output.pdf", plan)
    with pytest.raises(ValueError, match="DPI"):
        make_plan(path, dpi=2000)
    with pytest.raises(ValueError, match="no supported"):
        make_plan(path, align="center", dpi=72)


@pytest.mark.parametrize("mutation", ["stale", "duplicate", "nan", "review", "clipping", "skip"])
def test_invalid_plan_never_writes_pdf(source: Path, tmp_path: Path, mutation: str) -> None:
    plan = ready_plan(source)
    entry = plan["pages"][0]
    if mutation == "stale":
        plan["source"]["sha256"] = "0" * 64
    elif mutation == "duplicate":
        plan["pages"][1]["page"] = 1
    elif mutation == "nan":
        entry["rotation_deg"] = float("nan")
    elif mutation == "review":
        entry["status"] = "review"
    elif mutation == "clipping":
        entry["translate_x_pt"] = 1000
    else:
        entry["status"] = "skip"
        entry["translate_y_pt"] = 4
    output = tmp_path / "invalid.pdf"
    with pytest.raises(ValueError):
        apply_plan(source, output, plan)
    assert not output.exists()
    assert not output.with_suffix(".report.json").exists()


def test_existing_pdf_and_report_are_never_overwritten(source: Path, tmp_path: Path) -> None:
    plan = ready_plan(source)
    original_hash = file_digest(source)
    with pytest.raises(FileExistsError):
        apply_plan(source, source, plan)
    assert file_digest(source) == original_hash
    output = tmp_path / "new.pdf"
    report = output.with_suffix(".report.json")
    report.write_text("keep this")
    with pytest.raises(FileExistsError):
        apply_plan(source, output, plan)
    assert not output.exists()
    assert report.read_text() == "keep this"


def test_annotation_and_nonunit_geometry_are_rejected(source: Path, tmp_path: Path) -> None:
    annotated = tmp_path / "annotated.pdf"
    with pymupdf.open(source) as document:
        document[0].add_text_annot((60, 90), "Retain this annotation")
        document.save(annotated)
    with pytest.raises(ValueError, match="annotated"):
        apply_plan(annotated, tmp_path / "no-annotations.pdf", ready_plan(annotated))
    nonunit = tmp_path / "nonunit.pdf"
    writer = PdfWriter(clone_from=PdfReader(source))
    writer.pages[0][NameObject("/UserUnit")] = FloatObject(2)
    writer.write(nonunit)
    writer.close()
    with pytest.raises(ValueError, match="UserUnit"):
        apply_plan(nonunit, tmp_path / "no-userunit.pdf", ready_plan(nonunit))


def test_verify_detects_missing_page_and_changed_image(source: Path, tmp_path: Path) -> None:
    plan = ready_plan(source)
    output = tmp_path / "aligned.pdf"
    apply_plan(source, output, plan)
    missing = tmp_path / "missing.pdf"
    with pymupdf.open(output) as document:
        document.delete_page(1)
        document.save(missing)
    assert not verify_pdf(source, missing, plan)["passed"]
    altered = tmp_path / "altered.pdf"
    with pymupdf.open(output) as document:
        xref = document[0].get_images()[0][0]
        black = Image.new("L", (600, 800), "black")
        buffer = io.BytesIO()
        black.save(buffer, format="PNG")
        document[0].replace_image(xref, stream=buffer.getvalue())
        document.save(altered)
    report = verify_pdf(source, altered, plan)
    assert not report["passed"]
    assert not report["pages"][0]["images_preserved"]


def test_annotated_skip_retains_rotation_crop_and_annotation(source: Path, tmp_path: Path) -> None:
    annotated = tmp_path / "with-annotation.pdf"
    with pymupdf.open(source) as document:
        page = document[0]
        page.add_text_annot((60, 90), "Preserve this note")
        page.set_cropbox(pymupdf.Rect(20, 30, 280, 360))
        page.set_rotation(90)
        document.save(annotated)
    plan = make_plan(annotated, dpi=72)
    assert plan["pages"][0]["status"] == "skip"
    output = tmp_path / "preserved-annotation.pdf"
    assert apply_plan(annotated, output, plan)["passed"]
    with pymupdf.open(annotated) as before, pymupdf.open(output) as after:
        assert before[0].rotation == after[0].rotation == 90
        assert before[0].cropbox == after[0].cropbox
        assert before[0].first_annot.info["content"] == after[0].first_annot.info["content"]
        assert before[0].first_annot.rect == after[0].first_annot.rect
        assert before[0].get_pixmap().samples == after[0].get_pixmap().samples


@pytest.mark.parametrize("mutation", ["delete-note", "move-link", "change-target"])
def test_verify_detects_annotation_or_link_changes_on_skipped_page(
    source: Path, tmp_path: Path, mutation: str
) -> None:
    annotated = tmp_path / "annotated-source.pdf"
    with pymupdf.open(source) as document:
        page = document[0]
        page.add_text_annot((60, 90), "Preserve this note")
        page.insert_link(
            {
                "kind": pymupdf.LINK_URI,
                "from": pymupdf.Rect(40, 100, 200, 120),
                "uri": "https://example.org/",
            }
        )
        document.save(annotated)
    plan = make_plan(annotated, dpi=72)
    output = tmp_path / "valid-annotations.pdf"
    assert apply_plan(annotated, output, plan)["passed"]
    changed = tmp_path / "changed-annotations.pdf"
    with pymupdf.open(output) as document:
        page = document[0]
        if mutation == "delete-note":
            page.delete_annot(page.first_annot)
        else:
            link = page.get_links()[0]
            if mutation == "move-link":
                link["from"] = pymupdf.Rect(70, 140, 230, 160)
            else:
                link["uri"] = "https://example.org/changed"
            page.update_link(link)
        document.save(changed)
    report = verify_pdf(annotated, changed, plan)
    assert not report["pages"][0]["annotations_preserved"]
    assert not report["passed"]


@pytest.mark.parametrize("navigation", ["link", "bookmark", "named"])
def test_internal_destinations_pin_target_geometry(tmp_path: Path, navigation: str) -> None:
    source = make_source(tmp_path / "navigation.pdf", count=3)
    with pymupdf.open(source) as document:
        if navigation == "link":
            document[0].insert_link(
                {
                    "kind": pymupdf.LINK_GOTO,
                    "from": pymupdf.Rect(50, 60, 150, 80),
                    "page": 1,
                    "to": pymupdf.Point(80, 100),
                }
            )
        elif navigation == "bookmark":
            document.set_toc([[1, "Start", 2]])
        document.save(tmp_path / "with-navigation.pdf")
    source = tmp_path / "with-navigation.pdf"
    if navigation == "named":
        writer = PdfWriter(clone_from=PdfReader(source))
        writer.add_named_destination("section", 1)
        writer.write(tmp_path / "with-named-destination.pdf")
        writer.close()
        source = tmp_path / "with-named-destination.pdf"
    plan = make_plan(source, dpi=72)
    assert plan["pages"][1]["status"] == "skip"
    plan["pages"][2].update(status="ready", translate_x_pt=10.0, translate_y_pt=10.0)
    output = tmp_path / "safe-navigation.pdf"
    assert apply_plan(source, output, plan)["passed"]
    with pymupdf.open(source) as before, pymupdf.open(output) as after:
        if navigation == "link":
            assert before[0].get_links()[0]["to"] == after[0].get_links()[0]["to"]
        elif navigation == "bookmark":
            assert before.get_toc() == after.get_toc()
            old_destination, new_destination = (
                before.get_toc(False)[0][3],
                after.get_toc(False)[0][3],
            )
            for key in ("kind", "page", "to", "zoom"):
                assert old_destination[key] == new_destination[key]
        else:
            assert before.resolve_names() == after.resolve_names()
    plan["pages"][1].update(status="ready", translate_x_pt=10.0, translate_y_pt=10.0)
    with pytest.raises(ValueError, match="internal destination"):
        apply_plan(source, tmp_path / "unsafe-navigation.pdf", plan)
    assert not (tmp_path / "unsafe-navigation.pdf").exists()


def test_verify_detects_removed_bookmarks(tmp_path: Path) -> None:
    source = make_source(tmp_path / "with-toc.pdf")
    with pymupdf.open(source) as document:
        document.set_toc([[1, "Start", 1]])
        document.save(tmp_path / "toc-source.pdf")
    source = tmp_path / "toc-source.pdf"
    plan = make_plan(source, dpi=72)
    output = tmp_path / "toc-output.pdf"
    assert apply_plan(source, output, plan)["passed"]
    with pymupdf.open(output) as document:
        document.set_toc([])
        document.save(tmp_path / "without-toc.pdf")
    report = verify_pdf(source, tmp_path / "without-toc.pdf", plan)
    assert not report["navigation_preserved"]
    assert not report["passed"]


@pytest.mark.parametrize("action_kind", ["direct", "goto"])
def test_open_actions_are_rejected_before_planning(tmp_path: Path, action_kind: str) -> None:
    source = make_source(tmp_path / "ordinary.pdf")
    writer = PdfWriter(clone_from=PdfReader(source))
    destination = ArrayObject(
        [
            writer.pages[1].indirect_reference,
            NameObject("/XYZ"),
            FloatObject(80),
            FloatObject(300),
            NullObject(),
        ]
    )
    action = destination
    if action_kind == "goto":
        action = DictionaryObject(
            {NameObject("/S"): NameObject("/GoTo"), NameObject("/D"): destination}
        )
    writer.root_object[NameObject("/OpenAction")] = action
    writer.write(tmp_path / "open-action.pdf")
    writer.close()
    with pytest.raises(ValueError, match="OpenAction"):
        make_plan(tmp_path / "open-action.pdf", dpi=72)


def test_inspection_contact_sheets_include_arbitrary_page_counts(tmp_path: Path) -> None:
    source = make_source(tmp_path / "many.pdf", count=25)
    inventory = inspect_pdf(source, tmp_path / "run", dpi=36)
    assert len(inventory["pages"]) == 25
    assert len(inventory["contact_sheets"]) == 2
    assert (tmp_path / "run/previews/page-0025.png").is_file()
    assert inventory["source"]["filename"] == "many.pdf"
    assert "sha256" in inventory["source"]
    with pytest.raises(FileExistsError):
        inspect_pdf(source, tmp_path / "run", dpi=36)


def test_cli_end_to_end_and_clean_error(source: Path, tmp_path: Path, capsys) -> None:
    plan_path = tmp_path / "plan.json"
    assert main(["plan", str(source), "--output", str(plan_path), "--dpi", "72"]) == 0
    output = tmp_path / "cli.pdf"
    assert main(["apply", str(source), "--plan", str(plan_path), "--output", str(output)]) == 0
    assert main(["verify", str(source), str(output), "--plan", str(plan_path)]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["passed"]
    assert main(["apply", str(source), "--plan", str(plan_path), "--output", str(output)]) == 2
    assert "refusing to overwrite" in capsys.readouterr().err


def test_manual_plan_numeric_and_geometry_boundaries(source: Path) -> None:
    plan = ready_plan(source)
    for value in (True, "1.0", float("inf")):
        candidate = copy.deepcopy(plan)
        candidate["pages"][0]["translate_x_pt"] = value
        with pytest.raises(ValueError, match="finite number"):
            validate_plan(candidate, source)
    plan["pages"][0]["source_size_pt"] = [301, 400]
    with pytest.raises(ValueError, match="geometry"):
        validate_plan(plan, source)


def test_verify_rejects_unused_image_resource_after_visible_content_is_removed(
    tmp_path: Path,
) -> None:
    source = make_source(tmp_path / "image-only.pdf", count=1)
    with pymupdf.open(source) as document:
        # Make the fixture image-only so text checks cannot catch content loss.
        text_xref = document[0].get_contents()[-1]
        document.update_stream(text_xref, b"")
        document.save(tmp_path / "without-ocr.pdf")
    source = tmp_path / "without-ocr.pdf"
    plan = ready_plan(source)
    output = tmp_path / "valid.pdf"
    apply_plan(source, output, plan)
    broken = tmp_path / "unused-resource.pdf"
    with pymupdf.open(output) as document:
        for xref in document[0].get_contents():
            document.update_stream(xref, b"")
        document.save(broken)
    report = verify_pdf(source, broken, plan)
    assert report["pages"][0]["images_preserved"]
    assert not report["pages"][0]["render_matches_plan"]
    assert not report["passed"]


def test_corrupt_pdf_has_a_clean_cli_error(tmp_path: Path, capsys) -> None:
    source = tmp_path / "corrupt.pdf"
    source.write_text("This is not a PDF")
    assert main(["inspect", str(source), "--run-dir", str(tmp_path / "run")]) == 2
    assert "input PDF cannot be read" in capsys.readouterr().err
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("reported_bounds", [None, [149, 199, 151, 201]])
def test_edited_bounds_cannot_hide_source_clipping(tmp_path: Path, reported_bounds) -> None:
    source = make_source(tmp_path / "image-only-clipping.pdf", count=1, ocr=False)
    plan = ready_plan(source)
    plan["pages"][0]["content_bbox_pt"] = reported_bounds
    plan["pages"][0]["canvas_pt"] = [20.0, 20.0]
    plan["pages"][0]["translate_x_pt"] = -140.0
    plan["pages"][0]["translate_y_pt"] = -190.0
    with pytest.raises(ValueError, match="clips"):
        apply_plan(source, tmp_path / "clipped.pdf", plan)
