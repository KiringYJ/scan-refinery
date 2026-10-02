"""Conservative, image-only page geometry measurements.

The functions in this module only measure a grayscale raster.  They do not
modify it and deliberately return a zero angle when the available evidence is
too weak to justify a correction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Measurement:
    """A proposed page correction and the evidence supporting it.

    ``content_bbox`` uses pixel-edge coordinates: ``x1`` and ``y1`` are
    exclusive.  ``angle_deg`` is the observed downward-right baseline slope in
    image coordinates.  Rotating the image counterclockwise by that value
    flattens the measured feature.
    """

    angle_deg: float
    confidence: float
    method: str
    content_bbox: tuple[float, float, float, float] | None
    anchor: tuple[float, float] | None
    reasons: list[str]


def _foreground(image: np.ndarray) -> np.ndarray:
    """Return an unfiltered foreground mask, retaining isolated dark marks."""

    # Otsu adapts to lightly tinted scan backgrounds.  No connected-component
    # filter is applied here: an isolated full stop or accent must still expand
    # the content bounds.
    _threshold, mask = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    return mask > 0


def _content_bbox(mask: np.ndarray) -> tuple[float, float, float, float] | None:
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    return (
        float(xs.min()),
        float(ys.min()),
        float(xs.max() + 1),
        float(ys.max() + 1),
    )


def _validate_image(image: np.ndarray) -> None:
    if not isinstance(image, np.ndarray) or image.ndim != 2 or image.dtype != np.uint8:
        raise ValueError("image must be a two-dimensional uint8 grayscale array")
    if image.size == 0:
        raise ValueError("image must not be empty")


def content_bounds(image: np.ndarray) -> tuple[float, float, float, float] | None:
    """Return unfiltered dark-content bounds using exclusive maximum edges."""

    _validate_image(image)
    return _content_bbox(_foreground(image))


def _zero(
    method: str,
    bbox: tuple[float, float, float, float] | None,
    reason: str,
    *,
    confidence: float = 0.0,
) -> Measurement:
    return Measurement(0.0, confidence, method, bbox, None, [reason])


def _projection_measurement(
    mask: np.ndarray,
    bbox: tuple[float, float, float, float] | None,
    max_angle: float,
) -> Measurement:
    height, width = mask.shape
    foreground_count = int(mask.sum())
    area = height * width
    minimum_ink = max(64, round(area * 0.001))
    if bbox is None:
        return _zero("projection", bbox, "blank page: no dark foreground")
    if foreground_count < minimum_ink:
        return _zero(
            "projection",
            bbox,
            f"sparse page: {foreground_count} foreground pixels",
            confidence=0.05,
        )

    x0, y0, x1, y1 = (int(value) for value in bbox)
    content_width = x1 - x0
    content_height = y1 - y0
    if content_width < width * 0.2 or content_height < height * 0.12:
        return _zero(
            "projection",
            bbox,
            "sparse layout: content does not span enough of the page",
            confidence=0.1,
        )

    # Include a small margin so rotation does not immediately clip the content.
    pad_x = max(2, round(content_width * 0.02))
    pad_y = max(2, round(content_height * 0.02))
    crop = mask[
        max(0, y0 - pad_y) : min(height, y1 + pad_y),
        max(0, x0 - pad_x) : min(width, x1 + pad_x),
    ].astype(np.float32)

    # Bound work without making any detector threshold depend on a fixed DPI.
    scale = min(1.0, 1000.0 / max(crop.shape))
    if scale < 1.0:
        crop = cv2.resize(
            crop,
            (max(1, round(crop.shape[1] * scale)), max(1, round(crop.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )

    crop_height, crop_width = crop.shape
    center = ((crop_width - 1) / 2.0, (crop_height - 1) / 2.0)

    def score(angle: float) -> float:
        matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(
            crop,
            matrix,
            (crop_width, crop_height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        projection = rotated.sum(axis=1, dtype=np.float64)
        # Flat text baselines create abrupt, repeated changes in row mass.
        return float(np.square(np.diff(projection)).sum())

    coarse_step = min(0.05, max_angle / 20.0)
    coarse_angles = np.arange(-max_angle, max_angle + coarse_step / 2, coarse_step)
    coarse_scores = np.asarray([score(float(angle)) for angle in coarse_angles])
    coarse_index = int(np.argmax(coarse_scores))
    coarse_best = float(coarse_angles[coarse_index])

    fine_step = max(0.005, coarse_step / 10.0)
    fine_angles = np.arange(
        max(-max_angle, coarse_best - coarse_step),
        min(max_angle, coarse_best + coarse_step) + fine_step / 2,
        fine_step,
    )
    fine_scores = np.asarray([score(float(angle)) for angle in fine_angles])
    best_index = int(np.argmax(fine_scores))
    best_angle = float(fine_angles[best_index])
    best_score = float(fine_scores[best_index])

    # Compare the peak with angles far enough away that interpolation around a
    # real peak is not counted as competing evidence.
    exclusion = max(0.12, coarse_step * 2)
    competitors = fine_scores[np.abs(fine_angles - best_angle) >= exclusion]
    if competitors.size < 3:
        competitors = coarse_scores[np.abs(coarse_angles - best_angle) >= exclusion]
    competing_score = float(np.max(competitors)) if competitors.size else best_score
    prominence = max(0.0, (best_score - competing_score) / max(best_score, 1.0))

    # Several separated horizontal bands distinguish text from a single mark or
    # illustration.  The threshold is relative to the strongest occupied row.
    row_mass = crop.sum(axis=1)
    occupied = row_mass > max(2.0, float(np.max(row_mass)) * 0.08)
    transitions = np.diff(np.pad(occupied.astype(np.int8), (1, 1)))
    band_count = int(np.count_nonzero(transitions == 1))
    band_factor = min(1.0, band_count / 5.0)
    density = foreground_count / area
    density_factor = min(1.0, density / 0.015)
    confidence = float(np.clip(0.15 + 3.5 * prominence, 0.0, 1.0))
    confidence *= 0.55 + 0.25 * band_factor + 0.20 * density_factor

    reasons = [
        f"projection peak prominence {prominence:.3f}",
        f"{band_count} horizontal ink bands",
    ]
    if abs(best_angle) >= max_angle - fine_step * 1.5:
        confidence *= 0.45
        reasons.append("best angle lies at the configured search boundary")
    if prominence < 0.012 or band_count < 3 or confidence < 0.25:
        reasons.append("ambiguous projection evidence; angle left unchanged")
        return Measurement(0.0, min(confidence, 0.24), "projection", bbox, None, reasons)

    anchor = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    return Measurement(best_angle, min(confidence, 1.0), "projection", bbox, anchor, reasons)


def _header_rule_measurement(
    mask: np.ndarray,
    bbox: tuple[float, float, float, float] | None,
    max_angle: float,
) -> Measurement:
    height, width = mask.shape
    if bbox is None:
        return _zero("header-rule", bbox, "blank page: no dark foreground")

    top = mask[: max(1, round(height * 0.30))].astype(np.uint8) * 255
    lines = cv2.HoughLinesP(
        top,
        rho=1,
        theta=np.pi / 1800,
        threshold=max(16, round(width * 0.12)),
        minLineLength=max(12, round(width * 0.42)),
        maxLineGap=max(2, round(width * 0.025)),
    )
    candidates: list[tuple[float, float, float, float, float]] = []
    if lines is not None:
        for x0, y0, x1, y1 in lines.reshape(-1, 4):
            if x1 < x0:
                x0, y0, x1, y1 = x1, y1, x0, y0
            dx = float(x1 - x0)
            dy = float(y1 - y0)
            length = math.hypot(dx, dy)
            angle = math.degrees(math.atan2(dy, dx))
            midpoint_y = (y0 + y1) / 2.0
            # Ignore page-edge scan borders, which are not printed anchors.
            if length >= width * 0.42 and abs(angle) <= max_angle and midpoint_y >= height * 0.015:
                xs = np.arange(x0, x1 + 1)
                predicted_y = np.rint(y0 + (xs - x0) * dy / max(dx, 1.0)).astype(int)
                half_band = max(1, round(height * 0.0015))
                supported = np.zeros(xs.shape, dtype=bool)
                fitted_x: list[np.ndarray] = []
                fitted_y: list[np.ndarray] = []
                for offset in range(-half_band, half_band + 1):
                    ys = np.clip(predicted_y + offset, 0, top.shape[0] - 1)
                    ink = mask[ys, xs]
                    supported |= ink
                    fitted_x.append(xs[ink])
                    fitted_y.append(ys[ink])
                support = float(np.mean(supported))
                # Hough's allowed gap can join glyph edges into a long text-row
                # segment.  A printed rule remains dark almost continuously.
                if support >= 0.90:
                    slope, intercept = np.polyfit(
                        np.concatenate(fitted_x), np.concatenate(fitted_y), 1
                    )
                    fitted_angle = math.degrees(math.atan(float(slope)))
                    if abs(fitted_angle) <= max_angle:
                        anchor_x = (x0 + x1) / 2.0
                        anchor_y = float(slope * anchor_x + intercept)
                        candidates.append((length, support, fitted_angle, anchor_x, anchor_y))

    if not candidates:
        return _zero(
            "header-rule",
            bbox,
            "no sufficiently long continuous top-page rule within the angle bound",
            confidence=0.05,
        )

    length, support, angle, anchor_x, anchor_y = max(candidates, key=lambda item: item[0])
    length_ratio = min(1.0, length / width)
    confidence = float(np.clip(0.65 * support + 0.35 * length_ratio, 0.0, 1.0))
    reasons = [
        f"top-page rule spans {length_ratio:.1%} of page width",
        f"continuous dark-line support {support:.1%}",
    ]
    if confidence < 0.25:
        reasons.append("weak header-rule evidence; angle left unchanged")
        return Measurement(0.0, confidence, "header-rule", bbox, None, reasons)
    return Measurement(
        float(angle),
        confidence,
        "header-rule",
        bbox,
        (float(anchor_x), float(anchor_y)),
        reasons,
    )


def measure_image(
    image: np.ndarray, method: str = "projection", max_angle: float = 2.0
) -> Measurement:
    """Measure skew and content bounds in an 8-bit grayscale page image.

    Supported methods are ``projection`` for dense horizontal text and
    ``header-rule`` for a long printed line in the top 30 percent of the page.
    Weak, sparse, blank, or ambiguous evidence produces a low-confidence zero
    angle rather than an unsafe correction.
    """

    _validate_image(image)
    if not math.isfinite(max_angle) or max_angle <= 0:
        raise ValueError("max_angle must be a finite positive number")

    normalized_method = method.lower().replace("_", "-")
    if normalized_method not in {"projection", "header-rule"}:
        raise ValueError("method must be 'projection' or 'header-rule'")

    mask = _foreground(image)
    bbox = _content_bbox(mask)
    if normalized_method == "projection":
        return _projection_measurement(mask, bbox, float(max_angle))
    return _header_rule_measurement(mask, bbox, float(max_angle))
