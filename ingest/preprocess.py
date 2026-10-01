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
    key = cache.make_key(content_hash, max_edge, RUNTIME.jpeg_quality, is_dicom)
    info: dict = {"content_hash": content_hash, "warnings": []}

    def _render() -> bytes:
        if is_dicom:
            from ingest.dicom_utils import read_dicom

            image, dicom_meta = read_dicom(source, deidentify=RUNTIME.deidentify)
            info["dicom_metadata"] = dicom_meta
        else:
            image = Image.open(source)
            # Honour EXIF rotation; phone photos of films are often rotated.
            image = ImageOps.exif_transpose(image)
            if image.mode != "RGB":
                image = image.convert("RGB")

        info["original_size"] = list(image.size)

        longest = max(image.size)
        if longest > max_edge:
            scale = max_edge / longest
            new_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
            # LANCZOS preserves the fine texture radiology models rely on.
            image = image.resize(new_size, Image.LANCZOS)
            info["warnings"].append(
                f"downsampled {longest}px -> {max_edge}px for inference"
            )

        info["prepared_size"] = list(image.size)

        buffer = io.BytesIO()
        image.save(
            buffer,
            format="JPEG",
            quality=RUNTIME.jpeg_quality,
            optimize=True,
            subsampling=0,  # no chroma subsampling: keeps greyscale detail
        )
        return buffer.getvalue()

    prepared = cache.get_or_compute_bytes("prepared", key, _render, suffix=".jpg")

    # On a cache hit ``_render`` never ran, so fill dimensions from the file.
    if "prepared_size" not in info:
        with Image.open(prepared) as cached_image:
            info["prepared_size"] = list(cached_image.size)
            info["original_size"] = list(cached_image.size)
        info["cached"] = True

    return prepared, info


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
