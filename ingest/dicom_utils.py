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
import hashlib

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
    "InstanceNumber",
    "SeriesNumber",
    "NumberOfFrames",
    "SliceThickness",
    "SpacingBetweenSlices",
    "TemporalPositionIdentifier",
)

_GEOMETRY_TAGS = ("ImagePositionPatient", "ImageOrientationPatient", "PixelSpacing")
_UID_TAGS = ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID", "FrameOfReferenceUID")

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


def read_dicom(path: Path, deidentify: bool = True, frame_index: int | None = None):
    """Return ``(PIL.Image, metadata)`` for a DICOM file.

    Raises ``RuntimeError`` when pydicom is unavailable or the file has no
    decodable pixel data.
    """
    if not PYDICOM_AVAILABLE:
        raise RuntimeError(
            "pydicom is not installed; install it with `pip install pydicom pylibjpeg` "
            "to read .dcm files"
        )

    # Compatibility preview API. Study ingestion uses iter_dicom_frames and
    # includes every frame, rather than silently interpreting this preview.
    header = pydicom.dcmread(str(path), force=True, stop_before_pixels=True)
    count = max(1, int(getattr(header, "NumberOfFrames", 1) or 1))
    selected = count // 2 if frame_index is None else frame_index
    if not 0 <= selected < count:
        raise ValueError(f"frame index {selected} outside 0..{count - 1}")
    for index, image, metadata in iter_dicom_frames(path, deidentify=deidentify, indices=[selected]):
        if image is None:
            raise RuntimeError(metadata["decode_error"])
        return image, metadata
    raise RuntimeError("DICOM contains no decodable selected frame")


def iter_dicom_frames(path: Path, deidentify: bool = True, *, indices: list[int] | None = None):
    """Yield (zero-based index, image or None, metadata), preserving all frames.

    pydicom 3 streams frames; older versions fall back to a bounded pixel array.
    Failed/blank frames remain explicit records rather than disappearing.
    """
    if not PYDICOM_AVAILABLE:
        raise RuntimeError("pydicom is not installed; install pydicom to read .dcm files")
    from config import RUNTIME

    dataset = pydicom.dcmread(str(path), force=True)
    if not hasattr(dataset, "PixelData"):
        raise RuntimeError("DICOM file contains no PixelData")
    count = max(1, int(getattr(dataset, "NumberOfFrames", 1) or 1))
    # Validate decompressed size before allocating on compressed inputs too.
    decoded_bytes = (int(dataset.Rows) * int(dataset.Columns) * count *
                     int(getattr(dataset, "SamplesPerPixel", 1)) * int(dataset.BitsAllocated) // 8)
    if decoded_bytes > RUNTIME.max_dicom_decoded_mb * 1024 * 1024:
        raise RuntimeError(f"decoded DICOM exceeds MAOS_MAX_DICOM_DECODED_MB={RUNTIME.max_dicom_decoded_mb}")
    selected = range(count) if indices is None else indices
    metadata = _extract_metadata(dataset, deidentify=deidentify)
    try:
        from pydicom.pixels import iter_pixels
        frames = iter_pixels(dataset, indices=selected)
    except ImportError:  # pydicom 2 compatibility
        pixels = dataset.pixel_array
        frames = (pixels[index] if count > 1 else pixels for index in selected)
    try:
        for index, pixels in zip(selected, frames):
            info = dict(metadata, frames=count, frame_selected=index)
            frame_dataset = _frame_transform(dataset, index, info)
            try:
                yield index, _decode_pixels(pixels, frame_dataset, info), info
            except Exception as exc:
                info["decode_error"] = f"{type(exc).__name__}: {exc}"
                yield index, None, info
    except Exception as exc:
        raise RuntimeError(f"could not decode pixel data ({exc}); install pylibjpeg codecs for compressed DICOM") from exc


def _frame_transform(dataset, index, metadata):
    """Apply enhanced DICOM shared/per-frame geometry, LUT and VOI metadata."""
    groups = []
    shared = getattr(dataset, "SharedFunctionalGroupsSequence", [])
    if shared:
        groups.append(shared[0])
    per_frame = getattr(dataset, "PerFrameFunctionalGroupsSequence", [])
    if index < len(per_frame):
        groups.append(per_frame[index])
    for group in groups:
        for sequence, tags in (
            ("PlanePositionSequence", ("ImagePositionPatient",)),
            ("PlaneOrientationSequence", ("ImageOrientationPatient",)),
            ("PixelMeasuresSequence", ("PixelSpacing", "SliceThickness", "SpacingBetweenSlices")),
            ("PixelValueTransformationSequence", ("RescaleSlope", "RescaleIntercept")),
            ("FrameVOILUTSequence", ("WindowCenter", "WindowWidth")),
            ("FrameContentSequence", ("TemporalPositionIndex", "InStackPositionNumber", "StackID")),
        ):
            items = getattr(group, sequence, [])
            if not items:
                continue
            for tag in tags:
                value = getattr(items[0], tag, None)
                if value is not None:
                    if tag in {"RescaleSlope", "RescaleIntercept", "WindowCenter", "WindowWidth"}:
                        # Store transformation overrides on metadata instead of
                        # mutating scanner metadata shared by concurrent frames.
                        if type(value).__name__ == "MultiValue" or isinstance(value, (list, tuple)):
                            value = value[0]
                        metadata[tag] = float(value)
                    else:
                        metadata[tag] = [float(v) for v in value] if tag in _GEOMETRY_TAGS else str(value)
    return dataset


def _decode_pixels(pixels, dataset, metadata):
    import numpy as np
    from PIL import Image

    array = pixels.astype(np.float32)
    if getattr(dataset, "PhotometricInterpretation", "") == "PALETTE COLOR":
        try:
            from pydicom.pixels import apply_color_lut
        except ImportError:
            from pydicom.pixel_data_handlers.util import apply_color_lut
        color = apply_color_lut(pixels, dataset)
        if color.dtype.itemsize > 1:
            color = (color / 257).astype("uint8")
        return Image.fromarray(color).convert("RGB")
    if int(getattr(dataset, "SamplesPerPixel", 1)) > 1:
        # Color pixel arrays are already RGB (including YBR conversion in
        # pydicom); do not apply monochrome windowing or global rescaling.
        return Image.fromarray(np.clip(array, 0, 255).astype("uint8")).convert("RGB")

    # Modality LUT: stored values -> real-world units (e.g. Hounsfield).
    slope = float(metadata.get("RescaleSlope", getattr(dataset, "RescaleSlope", 1)) or 1)
    intercept = float(metadata.get("RescaleIntercept", getattr(dataset, "RescaleIntercept", 0)) or 0)
    if slope != 1 or intercept != 0:
        array = array * slope + intercept

    array = _apply_window(array, dataset, metadata)

    lo, hi = float(array.min()), float(array.max())
    if hi - lo < 1e-6:
        raise RuntimeError("DICOM pixel data is uniform (blank image)")
    if metadata.get("window"):
        window = metadata["window"]
        lo, hi = window["center"] - window["width"]/2, window["center"] + window["width"]/2
    # Fixed window scaling across slices retains the scanner's contrast, rather
    # than stretching every frame independently to unrelated min/max values.
    array = (array - lo) / (hi - lo) * 255.0
    if getattr(dataset, "PhotometricInterpretation", "") == "MONOCHROME1":
        array = 255.0 - array

    image = Image.fromarray(np.clip(array, 0, 255).astype("uint8"))
    if image.mode != "RGB":
        image = image.convert("RGB")
    return image


def _apply_window(array, dataset, metadata: dict[str, Any]):
    """Clip to the DICOM window (brightness/contrast the scanner intended)."""
    center = metadata.get("WindowCenter", getattr(dataset, "WindowCenter", None))
    width = metadata.get("WindowWidth", getattr(dataset, "WindowWidth", None))
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
    for tag in _GEOMETRY_TAGS:
        value = getattr(dataset, tag, None)
        if value is not None:
            metadata[tag] = [float(item) for item in value]
    for tag in _UID_TAGS:
        value = getattr(dataset, tag, None)
        if value:
            metadata[tag] = ("uid-" + hashlib.sha256(str(value).encode()).hexdigest()[:24]) if deidentify else str(value)

    if deidentify:
        metadata["deidentified"] = True
    else:
        for tag in _PHI_TAGS:
            value = getattr(dataset, tag, None)
            if value not in (None, ""):
                metadata[tag] = str(value)
    return metadata
