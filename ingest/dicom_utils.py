"""DICOM decoding.

Medical studies arrive as ``.dcm`` files whose pixel data is *not* display
ready: 12-16 bit integers that must be rescaled and windowed before any model
sees them. Feeding raw pixels to a vision model produces a black rectangle.

``pydicom`` is an optional dependency -- when it is absent we degrade to
"DICOM files are skipped" rather than crashing the whole run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.logging_utils import get_logger

_log = get_logger("dicom")

try:  # pragma: no cover - import guard
    import pydicom
    from pydicom.errors import InvalidDicomError

    PYDICOM_AVAILABLE = True
except ImportError:  # pragma: no cover
    pydicom = None  # type: ignore[assignment]
    InvalidDicomError = Exception  # type: ignore[assignment,misc]
    PYDICOM_AVAILABLE = False

# Header fields that are safe to keep and clinically useful.
_SAFE_TAGS = (
    "Modality",
    "BodyPartExamined",
    "StudyDescription",
    "SeriesDescription",
    "ViewPosition",
    "PatientAge",
    "PatientSex",
    "StudyDate",
    "Manufacturer",
    "PhotometricInterpretation",
    "Rows",
    "Columns",
)

# Direct identifiers stripped when MAOS_DEIDENTIFY is on.
_PHI_TAGS = (
    "PatientName",
    "PatientID",
    "PatientBirthDate",
    "PatientAddress",
    "PatientTelephoneNumbers",
    "OtherPatientIDs",
    "ReferringPhysicianName",
    "PerformingPhysicianName",
    "InstitutionName",
    "InstitutionAddress",
    "AccessionNumber",
    "StudyID",
)


def is_dicom(path: Path) -> bool:
    """Cheap magic-number check: byte 128..132 of a DICOM file is ``DICM``."""
    try:
        with open(path, "rb") as handle:
            handle.seek(128)
            return handle.read(4) == b"DICM"
    except OSError:
        return False


def read_dicom(path: Path, deidentify: bool = True):
    """Return ``(PIL.Image, metadata)`` for a DICOM file.

    Raises ``RuntimeError`` when pydicom is unavailable or the file has no
    decodable pixel data.
    """
    if not PYDICOM_AVAILABLE:
        raise RuntimeError(
            "pydicom is not installed; install it with `pip install pydicom pylibjpeg` "
            "to read .dcm files"
        )

    import numpy as np
    from PIL import Image

    try:
        dataset = pydicom.dcmread(str(path), force=True)
    except InvalidDicomError as exc:
        raise RuntimeError(f"not a readable DICOM file: {exc}") from exc

    if not hasattr(dataset, "PixelData"):
        raise RuntimeError("DICOM file contains no PixelData")

    try:
        pixels = dataset.pixel_array
    except Exception as exc:  # compressed transfer syntax without a codec
        raise RuntimeError(
            f"could not decode pixel data ({exc}); "
            "install `pylibjpeg pylibjpeg-libjpeg gdcm` for compressed DICOM"
        ) from exc

    metadata = _extract_metadata(dataset, deidentify=deidentify)

    # Multi-frame series (CT/MR): take the middle slice, which is the most
    # informative single frame, and record that we did so.
    if pixels.ndim == 3 and pixels.shape[-1] not in (3, 4):
        frame_count = pixels.shape[0]
        pixels = pixels[frame_count // 2]
        metadata["frames"] = frame_count
        metadata["frame_selected"] = frame_count // 2

    array = pixels.astype(np.float32)

    # Modality LUT: stored values -> real-world units (e.g. Hounsfield).
    slope = float(getattr(dataset, "RescaleSlope", 1) or 1)
    intercept = float(getattr(dataset, "RescaleIntercept", 0) or 0)
    if slope != 1 or intercept != 0:
        array = array * slope + intercept

    array = _apply_window(array, dataset, metadata)

    # MONOCHROME1 stores white as low values; invert so bone reads bright.
    if getattr(dataset, "PhotometricInterpretation", "") == "MONOCHROME1":
        array = array.max() - array

    lo, hi = float(array.min()), float(array.max())
    if hi - lo < 1e-6:
        raise RuntimeError("DICOM pixel data is uniform (blank image)")
    array = (array - lo) / (hi - lo) * 255.0

    image = Image.fromarray(array.astype("uint8"))
    if image.mode != "RGB":
        image = image.convert("RGB")
    return image, metadata


def _apply_window(array, dataset, metadata: dict[str, Any]):
    """Clip to the DICOM window (brightness/contrast the scanner intended)."""
    center = getattr(dataset, "WindowCenter", None)
    width = getattr(dataset, "WindowWidth", None)
    if center is None or width is None:
        return array

    # These tags may be multi-valued; the first entry is the default preset.
    if isinstance(center, (list, tuple)) or type(center).__name__ == "MultiValue":
        center = center[0]
    if isinstance(width, (list, tuple)) or type(width).__name__ == "MultiValue":
        width = width[0]

    try:
        center, width = float(center), float(width)
    except (TypeError, ValueError):
        return array
    if width <= 0:
        return array

    low, high = center - width / 2, center + width / 2
    metadata["window"] = {"center": center, "width": width}
    return array.clip(low, high)


def _extract_metadata(dataset, deidentify: bool = True) -> dict[str, Any]:
    """Pull the useful header fields, dropping direct identifiers."""
    metadata: dict[str, Any] = {}
    for tag in _SAFE_TAGS:
        value = getattr(dataset, tag, None)
        if value not in (None, ""):
            metadata[tag] = str(value)

    if deidentify:
        metadata["deidentified"] = True
    else:
        for tag in _PHI_TAGS:
            value = getattr(dataset, tag, None)
            if value not in (None, ""):
                metadata[tag] = str(value)
    return metadata
