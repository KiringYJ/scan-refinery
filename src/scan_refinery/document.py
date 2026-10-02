"""PDF inventory, source identity, and local preview artifacts."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pymupdf
from PIL import Image, ImageDraw

LOGGER = logging.getLogger(__name__)


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def json_bytes(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(json_bytes(data))


def open_document(source: Path) -> pymupdf.Document:
    try:
        document = pymupdf.open(source)
    except pymupdf.FileDataError as error:
        raise ValueError("input PDF cannot be read") from error
    if not document.is_pdf or document.needs_pass or document.page_count == 0:
        document.close()
        raise ValueError("input must be a nonempty, accessible PDF without a password")
    return document


def validate_dpi(dpi: int) -> None:
    if not 36 <= dpi <= 600:
        raise ValueError("preview DPI must be between 36 and 600")


def raster(page: pymupdf.Page, dpi: int, *, annots: bool = False) -> np.ndarray:
    validate_dpi(dpi)
    pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False, annots=annots)
    return np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width)


def source_identity(source: Path, document: pymupdf.Document) -> dict[str, Any]:
    return {
        "filename": source.name,
        "sha256": file_digest(source),
        "page_count": document.page_count,
    }


def image_fingerprints(document: pymupdf.Document, page: pymupdf.Page) -> list[str]:
    """Fingerprint compressed image streams, including any transparency masks."""
    fingerprints = []
    for image in page.get_images(full=True):
        for xref in (image[0], image[1]):
            if xref:
                stream = document.xref_stream_raw(xref)
                fingerprints.append(hashlib.sha256(stream).hexdigest())
    return sorted(fingerprints)


def _geometry_json(value: Any) -> Any:
    if isinstance(value, (pymupdf.Point, pymupdf.Rect)):
        return [_geometry_json(item) for item in value]
    if isinstance(value, float):
        # PDF serializers and MuPDF's float coordinates differ below a
        # thousandth of a point. Preserve visible geometry, not float spelling.
        return round(value, 3)
    if isinstance(value, dict):
        return {key: _geometry_json(item) for key, item in value.items() if key != "xref"}
    if isinstance(value, (list, tuple)):
        return [_geometry_json(item) for item in value]
    return value


def annotation_fingerprint(page: pymupdf.Page) -> str:
    """Compare annotation properties and link targets without unstable xref IDs."""
    document = page.parent
    if document is None:
        raise ValueError("annotation page must belong to an open document")
    annotations = []
    for annotation in page.annots():
        appearance_kind, appearance_value = document.xref_get_key(annotation.xref, "AP/N")
        appearance_digest = None
        if appearance_kind == "xref":
            stream = document.xref_stream_raw(int(appearance_value.split()[0]))
            if stream is not None:
                appearance_digest = hashlib.sha256(stream).hexdigest()
        annotations.append(
            {
                "type": annotation.type[0],
                "rect": list(annotation.rect),
                "info": annotation.info,
                "flags": annotation.flags,
                "colors": annotation.colors,
                "opacity": annotation.opacity,
                "border": annotation.border,
                "vertices": annotation.vertices,
                "appearance_kind": appearance_kind,
                "appearance_sha256": appearance_digest,
            }
        )
    snapshot = {
        "object_count": len(page.annot_xrefs()),
        "annotations": _geometry_json(annotations),
        "links": _geometry_json(page.get_links()),
    }
    canonical = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def navigation_fingerprint(document: pymupdf.Document) -> str:
    snapshot = _geometry_json(
        {"outlines": document.get_toc(simple=False), "named_destinations": document.resolve_names()}
    )
    canonical = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def inspect_pdf(source: Path, run_dir: Path, dpi: int = 144) -> dict[str, Any]:
    validate_dpi(dpi)
    with open_document(source) as document:
        identity = source_identity(source, document)
        run_dir.mkdir(parents=True, exist_ok=False)
        previews = run_dir / "previews"
        previews.mkdir()
        pages = []
        for index, page in enumerate(document):
            LOGGER.info("rendering page %s/%s", index + 1, document.page_count)
            page.get_pixmap(dpi=dpi, alpha=False).save(previews / f"page-{index + 1:04}.png")
            pages.append(
                {
                    "page": index + 1,
                    "label": page.get_label(),
                    "size_pt": [page.rect.width, page.rect.height],
                    "rotation_deg": page.rotation,
                    "mediabox": list(page.mediabox),
                    "cropbox": list(page.cropbox),
                    "image_count": len(page.get_images(full=True)),
                    "text_characters": len(page.get_text()),
                    "word_count": len(page.get_text("words")),
                    "annotations": len(page.annot_xrefs()),
                }
            )
        if file_digest(source) != identity["sha256"]:
            raise ValueError("source changed while inspecting; start a new run")
        contacts = _contact_sheets(previews, document.page_count, run_dir)
        inventory = {
            "schemaVersion": 1,
            "source": identity,
            "dpi": dpi,
            "pages": pages,
            "contact_sheets": contacts,
        }
        write_json(run_dir / "inventory.json", inventory)
        return inventory


def _contact_sheets(previews: Path, page_count: int, run_dir: Path) -> list[str]:
    contacts = []
    per_sheet, columns, cell_width, cell_height = 24, 4, 280, 360
    for start in range(0, page_count, per_sheet):
        count = min(per_sheet, page_count - start)
        rows = (count + columns - 1) // columns
        sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), "#eeeeee")
        draw = ImageDraw.Draw(sheet)
        for offset in range(count):
            index = start + offset
            x = (offset % columns) * cell_width
            y = (offset // columns) * cell_height
            with Image.open(previews / f"page-{index + 1:04}.png") as image:
                image.thumbnail((cell_width - 16, cell_height - 28))
                sheet.paste(image, (x + (cell_width - image.width) // 2, y + 22))
            draw.text((x + 10, y + 5), f"PDF page {index + 1}", fill="black")
        name = f"contact-{len(contacts) + 1:04}.jpg"
        sheet.save(run_dir / name, quality=90)
        contacts.append(name)
    return contacts
