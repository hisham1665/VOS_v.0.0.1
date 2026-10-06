"""Image preprocessing: decode once, resize once, cache forever.

The single largest avoidable cost in the original pipeline was base64-encoding
multi-megabyte source images straight into an API request. A 6.8MB JPEG becomes
~9MB of base64 and thousands of image tokens, while the model resizes it to
896px internally anyway.

Every image therefore passes through :func:`prepare_image` exactly once. The
result is keyed by content hash, so re-running a study is a cache hit.
"""

from __future__ import annotations

import io
from pathlib import Path

from config import RUNTIME
from core import cache
from core.logging_utils import get_logger

_log = get_logger("preprocess")


def prepare_image(
    source: Path,
    *,
    is_dicom: bool = False,
    max_edge: int | None = None,
) -> tuple[Path, dict]:
    """Return ``(prepared_path, info)`` for a model-ready JPEG copy of ``source``.

    ``info`` carries the original dimensions plus any non-fatal warnings.
    """
    from PIL import Image, ImageOps

    max_edge = max_edge or RUNTIME.max_image_edge
    content_hash = cache.hash_file(source)
    key = _prepared_key(content_hash, max_edge, is_dicom)
    cached = _cached_prepared(key)
    if cached:
        return cached
    if is_dicom:
        from ingest.dicom_utils import read_dicom
        image, metadata = read_dicom(source, deidentify=RUNTIME.deidentify)
    else:
        with Image.open(source) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
        metadata = {}
    return _prepare_decoded(image, content_hash, key, max_edge, metadata)


def _prepared_key(content_hash, max_edge, dicom, frame_index=None):
    return cache.make_key("prepared-v5", content_hash, max_edge, RUNTIME.jpeg_quality,
                          dicom, RUNTIME.deidentify if dicom else None, frame_index)


def _cached_prepared(key):
    cached = cache.read_json("prepared_info", key)
    if (isinstance(cached, dict) and isinstance(cached.get("path"), str)
            and isinstance(cached.get("info"), dict)):
        path = Path(cached["path"])
        if path.is_file() and path.stat().st_size:
            return path, {**cached["info"], "cached": True}
    return None


def _prepare_decoded(image, content_hash, key, max_edge, metadata):
    from PIL import Image

    info = {"content_hash": content_hash, "original_size": list(image.size), "warnings": [],
            "quality": assess_pixel_quality(image)}
    info["warnings"].extend(info["quality"]["issues"])
    if metadata:
        info["dicom_metadata"] = metadata
    longest = max(image.size)
    if longest > max_edge:
        scale = max_edge / longest
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
        info["warnings"].append(f"downsampled {longest}px -> {max_edge}px for inference")
    image = image.convert("RGB")
    info["prepared_size"] = list(image.size)

    def render():
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=RUNTIME.jpeg_quality, optimize=True, subsampling=0)
        return buffer.getvalue()

    prepared = cache.get_or_compute_bytes("prepared", key, render, suffix=".jpg")
    info["prepared_hash"] = cache.hash_file(prepared)
    # Cache both representations together: metadata/quality/dimensions must
    # survive hits and must never be inferred from a downsampled JPEG.
    cache.write_json("prepared_info", key, {"path": str(prepared.resolve()), "info": info})
    return prepared, info


def prepare_dicom_frames(source: Path):
    """Decode a DICOM object once, cache every frame and its original metadata."""
    from ingest.dicom_utils import iter_dicom_frames

    content_hash = cache.hash_file(source)
    max_edge = RUNTIME.max_image_edge
    manifest_key = _prepared_key(content_hash, max_edge, True, "all-frames")
    cached = cache.read_json("dicom_frames", manifest_key)
    if isinstance(cached, list) and cached and all(
        isinstance(row, dict) and isinstance(row.get("info"), dict) and isinstance(row.get("key"), str)
        and (row.get("error") or _cached_prepared(row["key"])) for row in cached
    ):
        for row in cached:
            if row.get("error"):
                yield None, row["info"]
            else:
                yield _cached_prepared(row["key"])
        return
    manifest = []
    for index, image, metadata in iter_dicom_frames(source, deidentify=RUNTIME.deidentify):
        frame_hash = content_hash if metadata["frames"] == 1 else cache.make_key(content_hash, "frame", index)
        key = _prepared_key(content_hash, max_edge, True, index)
        if image is None:
            info = {"dicom_metadata": metadata, "error": metadata["decode_error"]}
            manifest.append({"key": key, "info": info, "error": info["error"]})
            yield None, info
            continue
        prepared, info = _cached_prepared(key) or _prepare_decoded(image, frame_hash, key, max_edge, metadata)
        manifest.append({"key": key, "info": info})
        yield prepared, info
    if manifest:
        cache.write_json("dicom_frames", manifest_key, manifest)


def assess_pixel_quality(image) -> dict:
    """Conservative technical heuristics; never certify clinical image quality."""
    from PIL import ImageStat

    gray = image.convert("L")
    gray.thumbnail((128, 128))
    stats = ImageStat.Stat(gray)
    low, high = gray.getextrema()
    issues = []
    status = "unchecked_clinical_quality"
    if high == low:
        status = "unusable"
        issues.append("Uniform/blank image: no visible pixel detail")
    else:
        if min(image.size) < 128:
            issues.append("Low source resolution (<128 pixels on the shorter edge)")
        if stats.stddev[0] < 3:
            issues.append("Very low pixel contrast; review source windowing/exposure")
        if issues:
            status = "limited"
    return {"status": status, "issues": issues, "source_size": list(image.size),
            "pixel_range": [low, high], "grayscale_stddev": round(stats.stddev[0], 3),
            "scope": "technical pixel checks only; motion, positioning, exposure and diagnostic adequacy require visual review"}


def image_data_url(path: Path) -> str:
    """Base64 ``data:`` URL for a prepared image, for OpenAI-style APIs."""
    import base64
    import mimetypes

    mime, _ = mimetypes.guess_type(str(path))
    payload = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return f"data:{mime or 'image/jpeg'};base64,{payload}"


def load_pil(path: Path):
    """Open a prepared image as RGB PIL, for local transformers pipelines."""
    from PIL import Image

    image = Image.open(path)
    return image.convert("RGB") if image.mode != "RGB" else image
