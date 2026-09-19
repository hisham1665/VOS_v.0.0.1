"""Quality detection and scoring (plan Phase 3 quality checks + quality scoring).

Detects: blurry, low resolution, excessive darkness, excessive brightness,
rotation, skew, cropping, blank pages, duplicate pages. Pure-Pillow
implementations of the standard computer-vision heuristics (Laplacian variance
for blur, dHash for duplicates, projection statistics for the rest).
"""

from __future__ import annotations

from typing import List, Optional

from PIL import Image, ImageFilter

from .models import PageIssue, PageMetrics, PageStatus
from .normalize import (
    _BINARIZE_THRESHOLD,
    _variance,
    detect_rotation,
    detect_skew,
    to_grayscale_thumb,
)

# -- thresholds (documented heuristics, tunable) --------------------------
MIN_ACCEPTABLE_DPI = 150.0
BLANK_MAX_STDDEV = 5.0
BLANK_MIN_MEAN = 246.0
DARK_MAX_MEAN = 90.0
WASHED_MIN_MEAN = 235.0
WASHED_MAX_STDDEV = 12.0
BLUR_THUMB_WIDTH = 480
BLUR_VARIANCE_THRESHOLD = 200.0
CROP_BORDER_FRACTION = 0.025
CROP_BORDER_INK_THRESHOLD = 0.20
MIN_ROTATION_CONFIDENCE = 0.5
MAX_DESKEW_DEGREES = 1.0
HASH_DUPLICATE_WIDTH = 9
HASH_DUPLICATE_HEIGHT = 8
DUPLICATE_HAMMING_THRESHOLD = 8

# Penalties per detected issue (score starts at 1.0).
SCORE_PENALTIES = {
    PageIssue.BLURRY: 0.30,
    PageIssue.LOW_RESOLUTION: 0.25,
    PageIssue.EXCESSIVE_DARKNESS: 0.25,
    PageIssue.EXCESSIVE_BRIGHTNESS: 0.25,
    PageIssue.ROTATED: 0.20,
    PageIssue.SKEWED: 0.15,
    PageIssue.CROPPED: 0.15,
    PageIssue.BLANK: 0.45,
    PageIssue.DUPLICATE: 0.35,
}

REVIEW_SCORE = 0.75
FAIL_SCORE = 0.40


def luminance_stats(gray: Image.Image) -> tuple[float, float]:
    pixels = list(gray.get_flattened_data())
    mean = sum(pixels) / len(pixels)
    variance = _variance([float(p) for p in pixels])
    return mean, variance ** 0.5


def is_blank(mean: float, stddev: float) -> bool:
    return mean >= BLANK_MIN_MEAN and stddev < BLANK_MAX_STDDEV


def blur_variance(image: Image.Image) -> float:
    """Variance of the Laplacian response on a fixed-width downscale."""
    thumb = to_grayscale_thumb(image, BLUR_THUMB_WIDTH)
    laplacian = thumb.filter(
        ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)
    )
    pixels = [float(p) for p in laplacian.get_flattened_data()]
    return _variance(pixels)


def border_ink_stats(image: Image.Image) -> tuple[float, float]:
    """(border, interior) dark-ink fractions on a 256-wide thumb.

    Cropping is a *relative* condition: content cut off at an edge makes the
    border strip denser than the interior, so a uniformly dark page (whose
    border and interior look alike) is not misread as cropped.
    """
    thumb = to_grayscale_thumb(image, 256)
    w, h = thumb.size
    border = max(1, round(min(w, h) * CROP_BORDER_FRACTION))
    pixels = thumb.load()
    border_dark = interior_dark = border_total = interior_total = 0
    for y in range(h):
        for x in range(w):
            on_border = (
                x < border or x >= w - border or y < border or y >= h - border
            )
            if on_border:
                if pixels[x, y] < _BINARIZE_THRESHOLD:
                    border_dark += 1
                border_total += 1
            else:
                if pixels[x, y] < _BINARIZE_THRESHOLD:
                    interior_dark += 1
                interior_total += 1
    border_ratio = border_dark / border_total if border_total else 0.0
    interior_ratio = interior_dark / interior_total if interior_total else 0.0
    return border_ratio, interior_ratio


def page_hash(image: Image.Image) -> int:
    """64-bit differential hash (dHash): identical pages hash identically."""
    thumb = to_grayscale_thumb(image, HASH_DUPLICATE_WIDTH)
    thumb = thumb.resize(
        (HASH_DUPLICATE_WIDTH, HASH_DUPLICATE_HEIGHT), Image.Resampling.BILINEAR
    )
    pixels = list(thumb.get_flattened_data())
    h = 0
    for y in range(HASH_DUPLICATE_HEIGHT):
        for x in range(HASH_DUPLICATE_WIDTH - 1):
            h = (h << 1) | (1 if pixels[y * HASH_DUPLICATE_WIDTH + x] > pixels[y * HASH_DUPLICATE_WIDTH + x + 1] else 0)
    return h


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def score_page(
    image: Image.Image,
    *,
    dpi: float = 150.0,
    rotation_degrees: int = 0,
    rotation_confidence: float = 0.0,
    skew_degrees: float = 0.0,
) -> tuple[PageMetrics, List[PageIssue], float]:
    """Measure one page and return (metrics, detected issues, quality score)."""
    gray = to_grayscale_thumb(image, 256)
    w, h = image.size
    mean_luma, stddev_luma = luminance_stats(gray)
    blur_var = blur_variance(image)
    border_ink, interior_ink = border_ink_stats(image)

    metrics = PageMetrics(
        width=w,
        height=h,
        dpi=dpi,
        mean_luma=round(mean_luma, 2),
        stddev_luma=round(stddev_luma, 2),
        blur_variance=round(blur_var, 2),
        border_ink_ratio=round(border_ink, 4),
        rotation_degrees=rotation_degrees,
        rotation_confidence=round(rotation_confidence, 3),
        skew_degrees=round(skew_degrees, 3),
    )

    issues: List[PageIssue] = []
    if is_blank(mean_luma, stddev_luma):
        issues.append(PageIssue.BLANK)
    if mean_luma < DARK_MAX_MEAN:
        issues.append(PageIssue.EXCESSIVE_DARKNESS)
    if (not is_blank(mean_luma, stddev_luma)
            and mean_luma >= WASHED_MIN_MEAN
            and stddev_luma < WASHED_MAX_STDDEV):
        issues.append(PageIssue.EXCESSIVE_BRIGHTNESS)
    if dpi < MIN_ACCEPTABLE_DPI:
        issues.append(PageIssue.LOW_RESOLUTION)
    if blur_var < BLUR_VARIANCE_THRESHOLD and not issues:
        issues.append(PageIssue.BLURRY)
    if border_ink > CROP_BORDER_INK_THRESHOLD and border_ink > interior_ink:
        issues.append(PageIssue.CROPPED)
    if rotation_degrees != 0 and rotation_confidence >= MIN_ROTATION_CONFIDENCE:
        issues.append(PageIssue.ROTATED)
    if abs(skew_degrees) > MAX_DESKEW_DEGREES:
        issues.append(PageIssue.SKEWED)

    score = max(0.0, 1.0 - sum(SCORE_PENALTIES[i] for i in issues))
    return metrics, issues, round(score, 4)


def score_from_issues(issues: List[PageIssue]) -> float:
    return round(max(0.0, 1.0 - sum(SCORE_PENALTIES[i] for i in issues)), 4)


def status_for_score(score: float) -> PageStatus:
    if score >= REVIEW_SCORE:
        return PageStatus.OK
    if score >= FAIL_SCORE:
        return PageStatus.REVIEW
    return PageStatus.REJECTED