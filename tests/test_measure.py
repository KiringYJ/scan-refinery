from __future__ import annotations

import cv2
import numpy as np
import pytest

from scan_refinery.measure import Measurement, content_bounds, measure_image


def _dense_text_page(angle: float = 0.0) -> np.ndarray:
    page = np.full((720, 520), 255, dtype=np.uint8)
    for row, y in enumerate(range(125, 610, 42)):
        x = 58 + (row % 3) * 8
        cv2.putText(
            page,
            "Algebra 123 theorem proof",
            (x, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            0,
            1,
            cv2.LINE_AA,
        )
    if angle:
        center = ((page.shape[1] - 1) / 2, (page.shape[0] - 1) / 2)
        matrix = cv2.getRotationMatrix2D(center, -angle, 1.0)
        page = cv2.warpAffine(
            page,
            matrix,
            (page.shape[1], page.shape[0]),
            flags=cv2.INTER_CUBIC,
            borderValue=255,
        )
    return page


def _add_gapped_text_row(page: np.ndarray, y: int) -> None:
    """Draw a long glyph-like row whose gaps Hough is allowed to bridge."""

    for x in range(32, page.shape[1] - 32, 13):
        cv2.rectangle(page, (x, y - 13), (x + 6, y), 0, -1)


@pytest.mark.parametrize("angle", [0.75, -1.1])
def test_projection_recovers_signed_dense_text_skew(angle: float) -> None:
    measurement = measure_image(_dense_text_page(angle), max_angle=1.8)

    assert isinstance(measurement, Measurement)
    assert measurement.method == "projection"
    assert measurement.angle_deg == pytest.approx(angle, abs=0.10)
    assert measurement.confidence >= 0.25
    assert measurement.anchor is not None


def test_projection_keeps_straight_dense_text_straight() -> None:
    measurement = measure_image(_dense_text_page())

    assert measurement.angle_deg == pytest.approx(0.0, abs=0.06)
    assert measurement.confidence >= 0.25


@pytest.mark.parametrize(
    "page, expected_reason",
    [
        (np.full((400, 300), 255, dtype=np.uint8), "blank"),
        (
            np.pad(np.zeros((2, 2), dtype=np.uint8), ((199, 199), (149, 149)), constant_values=255),
            "sparse",
        ),
    ],
)
def test_projection_leaves_blank_and_sparse_pages_unchanged(
    page: np.ndarray, expected_reason: str
) -> None:
    measurement = measure_image(page)

    assert measurement.angle_deg == 0.0
    assert measurement.confidence < 0.25
    assert any(expected_reason in reason for reason in measurement.reasons)


def test_header_rule_reports_angle_and_anchor() -> None:
    page = np.full((700, 500), 255, dtype=np.uint8)
    x0, x1 = 55, 445
    angle = 0.8
    y0 = 92
    y1 = round(y0 + np.tan(np.deg2rad(angle)) * (x1 - x0))
    cv2.line(page, (x0, y0), (x1, y1), 0, 2, cv2.LINE_AA)
    cv2.putText(page, "RUNNING HEADER", (140, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.5, 0, 1)

    measurement = measure_image(page, method="header-rule")

    assert measurement.angle_deg == pytest.approx(angle, abs=0.12)
    assert measurement.confidence >= 0.75
    assert measurement.anchor == pytest.approx(((x0 + x1) / 2, (y0 + y1) / 2), abs=3)


def test_header_rule_prefers_continuous_rule_over_longer_gapped_text_row() -> None:
    page = np.full((700, 500), 255, dtype=np.uint8)
    _add_gapped_text_row(page, 155)
    x0, x1 = 120, 380
    angle = -0.7
    y0 = 84
    y1 = round(y0 + np.tan(np.deg2rad(angle)) * (x1 - x0))
    cv2.line(page, (x0, y0), (x1, y1), 0, 2, cv2.LINE_AA)

    measurement = measure_image(page, method="header-rule")

    assert measurement.angle_deg == pytest.approx(angle, abs=0.15)
    assert measurement.anchor == pytest.approx(((x0 + x1) / 2, (y0 + y1) / 2), abs=3)
    assert any("continuous dark-line support" in reason for reason in measurement.reasons)


def test_header_rule_missing_returns_low_confidence_zero() -> None:
    page = np.full((700, 500), 255, dtype=np.uint8)
    for y in (80, 125, 170):
        _add_gapped_text_row(page, y)

    measurement = measure_image(page, method="header-rule")

    assert measurement.angle_deg == 0.0
    assert measurement.confidence < 0.25
    assert measurement.anchor is None
    assert any("no sufficiently long" in reason for reason in measurement.reasons)


def test_content_bbox_retains_extremal_tiny_marks() -> None:
    page = _dense_text_page()
    page[3, 4] = 0
    page[-5, -6] = 0

    measurement = measure_image(page)

    assert measurement.content_bbox == (4.0, 3.0, 515.0, 716.0)
    assert content_bounds(page) == measurement.content_bbox


def test_rejects_non_grayscale_input_and_unknown_method() -> None:
    with pytest.raises(ValueError, match="grayscale"):
        measure_image(np.zeros((10, 10, 3), dtype=np.uint8))
    with pytest.raises(ValueError, match="grayscale"):
        content_bounds(np.zeros((10, 10, 3), dtype=np.uint8))
    with pytest.raises(ValueError, match="method"):
        measure_image(np.zeros((10, 10), dtype=np.uint8), method="unknown")
    with pytest.raises(ValueError, match="empty"):
        content_bounds(np.empty((0, 0), dtype=np.uint8))
