"""Image normalization, rotation detection and deskew (plan Phase 3 pipeline).

Heavy numerics (numpy/OpenCV) are *not* required: everything runs on Pillow
only, using small downsamples and bright/dark projection heuristics, which keeps
the intake service dependency-light and testable. The heuristics assume
left-to-right text pages:

* A correctly oriented text page has horizontal text lines, so its *row*
  ink profile carries high variance vs its *column* profile.
* Deskew measures the rotation angle (inside a small sweep) that maximizes row
  profile variance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from PIL import Image, ImageFilter, ImageOps

_BINARIZE_THRESHOLD = 165
_ROTATION_THUMB_WIDTH = 160
_ROTATION_INK_MIN = 0.01
_SKEW_THUMB_WIDTH = 128
_SKEW_RANGE = 5.0
_SKEW_STEP = 0.25
_SKEW_MIN_IMPROVEMENT = 0.03  # relative row-variance gain to trust a correction


@dataclass
class RotationResult:
    """Detected corrective rotation (PIL convention: +90 is counterclockwise)."""

    degrees: int  # 0, 90 or -90
    confidence: float  # 0..1
    transpose: Optional[Image.Transpose] = None


def normalize_base(image: Image.Image, max_dim: int = 8192) -> Image.Image:
    """Normalize an extracted page: EXIF-orient, RGB, dimension cap."""
    image = ImageOps.exif_transpose(image)
    if image.mode != "RGB":
        image = image.convert("RGB")
    if max(image.size) > max_dim:
        image = image.resize(
            _scale_keep(image.size, max_dim), Image.Resampling.LANCZOS
        )
    return image


def to_grayscale_thumb(image: Image.Image, width: int) -> Image.Image:
    gray = image.convert("L")
    height = max(1, round(image.height * width / image.width))
    return gray.resize((width, height), Image.Resampling.BILINEAR)


def _scale_keep(size: tuple[int, int], max_dim: int) -> tuple[int, int]:
    w, h = size
    if w >= h:
        return (max_dim, max(1, round(h * max_dim / w)))
    return (max(1, round(w * max_dim / h)), max_dim)


def _horizontal_text_score(gray: Image.Image) -> tuple[float, float]:
    """(row_variance, col_variance) of dark ink in a small grayscale image."""
    w, h = gray.size
    pixels = list(gray.get_flattened_data())
    ink = [255 - p for p in pixels]
    rows: List[float] = []
    cols: List[float] = [0.0] * w
    for y in range(h):
        total = 0.0
        base_i = y * w
        for x in range(w):
            v = ink[base_i + x]
            total += v
            cols[x] += v
        rows.append(total)
    row_var = _variance(rows)
    col_var = _variance(cols)
    return row_var, col_var


def _variance(values: List[float]) -> float:
    if not values:
        return 0.0
    n = len(values)
    mean = sum(values) / n
    return sum((v - mean) ** 2 for v in values) / n


def _ink_fraction(gray: Image.Image) -> float:
    pixels = list(gray.get_flattened_data())
    dark = sum(1 for p in pixels if p < _BINARIZE_THRESHOLD)
    return dark / len(pixels)


def _ink_centroid_row(gray: Image.Image) -> float:
    """Mean dark-pixel row (0 = top); tie-breaks +90 vs -90 rotation."""
    w, h = gray.size
    pixels = list(gray.get_flattened_data())
    total = 0
    weighted = 0
    for y in range(h):
        base_i = y * w
        for x in range(w):
            if pixels[base_i + x] < _BINARIZE_THRESHOLD:
                total += 1
                weighted += y
    return (weighted / total) if total else h / 2.0


def _resolve_side_direction(
    thumb: Image.Image,
    primary: tuple[int, Image.Transpose],
    alternate: tuple[int, Image.Transpose],
) -> tuple[int, Image.Transpose]:
    """Pick the sideways correction whose result keeps ink biased to the top."""
    primary_variant = thumb.transpose(primary[1])
    alternate_variant = thumb.transpose(alternate[1])
    if _ink_centroid_row(primary_variant) <= _ink_centroid_row(alternate_variant):
        return primary
    return alternate


def detect_rotation(image: Image.Image) -> RotationResult:
    """Return the corrective rotation (0, +90 or -90) and a confidence score.

    A page is judged sideways when rotating it 90 deg yields *more horizontal*
    ink profiles (rotating 90 deg flips which projection carries text lines).
    The two sideways directions always share that statistic, so the direction
    is chosen by which candidate keeps ink clustered toward the top (headers)
    -- a heuristic that prefers the reading orientation for typical sheets.
    180-degree (upside-down) pages are indistinguishable by projection and
    therefore report 0; flagging those is left to OCR (Phase 4).
    """
    thumb = to_grayscale_thumb(image, _ROTATION_THUMB_WIDTH)
    if _ink_fraction(thumb) < _ROTATION_INK_MIN:
        return RotationResult(degrees=0, confidence=0.0)

    upright_row, upright_col = _horizontal_text_score(thumb)
    upright_score = upright_row - upright_col

    candidates = (
        (90, Image.Transpose.ROTATE_90),
        (-90, Image.Transpose.ROTATE_270),
    )
    scored: List[tuple[float, int, Image.Transpose]] = []
    for degrees, transpose in candidates:
        variant = thumb.transpose(transpose)
        row_var, col_var = _horizontal_text_score(variant)
        scored.append((row_var - col_var, degrees, transpose))

    scored.sort(key=lambda s: s[0], reverse=True)
    side_score, side_degrees, side_transpose = scored[0]

    if side_score <= upright_score:
        return RotationResult(degrees=0, confidence=0.0)

    alternate_degrees, alternate_transpose = scored[1][1], scored[1][2]
    degrees, transpose = _resolve_side_direction(
        thumb,
        (side_degrees, side_transpose),
        (alternate_degrees, alternate_transpose),
    )
    confidence = max(
        0.0,
        min(1.0, (side_score - upright_score) / (1.0 + abs(side_score))),
    )
    return RotationResult(degrees=degrees, confidence=confidence, transpose=transpose)


def detect_skew(image: Image.Image) -> float:
    """Rotation angle (degrees) that straightens the text, or 0.0 if straight.

    The angle is trusted only when the row-variance gain over the identity
    orientation is at least `_SKEW_MIN_IMPROVEMENT` (relative); this keeps
    low-contrast or noise-dominated pages from producing sweep-edge artifacts.
    """
    thumb = to_grayscale_thumb(image, _SKEW_THUMB_WIDTH)
    _, base_variance = _horizontal_text_score(thumb)
    best_angle = 0.0
    best_score = base_variance
    angle = -_SKEW_RANGE
    while angle <= _SKEW_RANGE:
        if abs(angle) >= 1e-9:
            variant = thumb.rotate(
                angle, resample=Image.Resampling.BILINEAR, fillcolor=255
            )
            row_var, _ = _horizontal_text_score(variant)
            if row_var > best_score:
                best_score = row_var
                best_angle = angle
        angle = round(angle + _SKEW_STEP, 3)
    if best_angle != 0.0 and best_score < base_variance * (1.0 + _SKEW_MIN_IMPROVEMENT):
        return 0.0
    return round(best_angle, 3)


def deskew(image: Image.Image, correction: float) -> Image.Image:
    """Apply the skew correction to a full-resolution page."""
    if abs(correction) < 1e-9:
        return image
    return image.rotate(
        correction, resample=Image.Resampling.BICUBIC, fillcolor=(255, 255, 255)
    )