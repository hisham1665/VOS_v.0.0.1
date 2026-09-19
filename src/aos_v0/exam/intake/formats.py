"""File validation and page extraction (plan Phase 3: upload -> validation -> extraction).

Supported inputs: PDF, JPG, JPEG, PNG, TIFF, ZIP, folder. Validation uses magic
bytes (not just extensions), extraction turns one source into a list of
:class:`Frame` tuples carrying the PIL image and the identity label used in the
page metadata system.

PDF rasterization requires ``pypdfium2``; missing it raises an actionable
:class:`IntakeError`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import List, Optional, Tuple
from zipfile import ZipFile

from PIL import Image, UnidentifiedImageError

from .models import IntakeError, UnsupportedFormatError

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".bmp",
    ".webp",
}

ZIP_MAGIC = b"PK\x03\x04"
ZIP_EMPTY_MAGIC = b"PK\x05\x06"
PDF_MAGIC = b"%PDF"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
TIFF_MAGIC_LE = b"II*\x00"
TIFF_MAGIC_BE = b"MM\x00*"

_MAX_READ_BYTES = 16 * 1024


@dataclass
class Frame:
    """One extracted page image plus its identity."""

    label: str
    image: Image.Image
    member: Optional[str] = None
    declared_no: Optional[int] = None

    SORTER = staticmethod(lambda f: _natural_key(f.label))


def sniff_kind(path: Path) -> str:
    """Classify a file by magic bytes: 'pdf' | 'image' | 'zip' | 'unknown'."""
    with open(path, "rb") as fh:
        head = fh.read(_MAX_READ_BYTES)
    if head.startswith(PDF_MAGIC):
        return "pdf"
    if head.startswith((ZIP_MAGIC, ZIP_EMPTY_MAGIC)):
        return "zip"
    if head.startswith((PNG_MAGIC, JPEG_MAGIC, TIFF_MAGIC_LE, TIFF_MAGIC_BE)):
        return "image"
    try:
        with Image.open(path) as im:
            im.verify()
        return "image"
    except Exception:
        return "unknown"


def _natural_key(text: str) -> List[object]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text)]


def _declared_no_from_member(member: str) -> Optional[int]:
    stem = Path(member).stem
    match = re.search(r"(\d+)$", stem)
    if not match:
        return None
    return int(match.group(1))


def _extract_pdf(path: Path, dpi: float, max_pages: int) -> Tuple[List[Frame], bool]:
    truncated = False
    try:
        import pypdfium2 as pdfium  # type: ignore[import-not-found,import-untyped]
    except ImportError as exc:  # pragma: no cover - env-specific
        raise IntakeError(
            "PDF intake requires 'pypdfium2' (pip install pypdfium2)"
        ) from exc

    try:
        doc = pdfium.PdfDocument(str(path))
    except Exception as exc:  # noqa: BLE001 - PdfiumError family
        raise IntakeError(f"damaged or unreadable PDF {path.name}: {exc}") from exc

    try:
        page_count = len(doc)
        if page_count == 0:
            raise IntakeError(f"PDF {path.name} contains no pages")

        want = page_count if max_pages <= 0 else min(page_count, max_pages)
        truncated = want < page_count
        scale = dpi / 72.0
        frames: List[Frame] = []
        for i in range(want):
            try:
                bitmap = doc[i].render(scale=scale)
                image = bitmap.to_pil()
                if image.mode != "RGB":
                    image = image.convert("RGB")
            except Exception as exc:  # noqa: BLE001
                raise IntakeError(
                    f"failed to render page {i + 1} of {path.name}: {exc}"
                ) from exc
            frames.append(
                Frame(
                    label=f"{path.name} p{i + 1}",
                    image=image,
                    member=f"p{i + 1}",
                    declared_no=i + 1,
                )
            )
        return frames, truncated
    finally:
        doc.close()


def _extract_image_file(path: Path, label: str, member: Optional[str] = None) -> List[Frame]:
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        raise UnsupportedFormatError(
            f"unsupported image extension '.{path.suffix.lstrip('.')}' for {label}"
        )
    member = member or path.name
    try:
        with Image.open(path) as im:
            frame_count = getattr(im, "n_frames", 1)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise IntakeError(f"damaged image {label}: {exc}") from exc

    frames: List[Frame] = []
    try:
        with Image.open(path) as im:
            for i in range(frame_count):
                im.seek(i)
                im.load()
                frame = im.copy()
                if frame.mode not in ("RGB", "L", "1"):
                    frame = frame.convert("RGB")
                name = f"{label} [{i + 1}/{frame_count}]" if frame_count > 1 else label
                frames.append(
                    Frame(
                        label=name,
                        image=frame,
                        member=member,
                        declared_no=_declared_no_from_member(member),
                    )
                )
    except Exception as exc:  # noqa: BLE001
        raise IntakeError(f"damaged image {label}: {exc}") from exc
    return frames


def _extract_zip(path: Path, max_pages: int) -> Tuple[List[Frame], List[str], bool]:
    skipped: List[str] = []
    members: List[str] = []
    try:
        with ZipFile(path) as zf:
            members = [m for m in zf.namelist() if not m.endswith("/")]
    except Exception as exc:  # noqa: BLE001
        raise IntakeError(f"damaged ZIP archive {path.name}: {exc}") from exc

    # Archive order is the physical scan order: keep it as-is so the page
    # ordering pass can detect out-of-order scans. Folders, by contrast, are
    # natural-sorted in _extract_folder.
    frames: List[Frame] = []
    skipped: List[str] = []
    truncated = False
    for member in members:
        ext = Path(member).suffix.lower()
        if ext not in IMAGE_EXTENSIONS:
            skipped.append(f"{member}: unsupported type")
            continue
        if len(frames) >= max_pages:
            truncated = True
            break
        try:
            with ZipFile(path) as zf:
                raw = zf.read(member)
            frames.extend(_frames_from_bytes(raw, member))
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"{member}: damaged ({exc})")
            continue
    return frames, skipped, truncated


def _frames_from_bytes(data: bytes, member: str) -> List[Frame]:
    """Open an in-memory image (possibly multi-frame) into frames."""
    with Image.open(BytesIO(data)) as probe:
        frame_count = getattr(probe, "n_frames", 1)
    frames: List[Frame] = []
    image = Image.open(BytesIO(data))
    try:
        for i in range(frame_count):
            image.seek(i)
            image.load()
            frame = image.copy()
            if frame.mode not in ("RGB", "L", "1"):
                frame = frame.convert("RGB")
            name = f"{member} [{i + 1}/{frame_count}]" if frame_count > 1 else member
            frames.append(
                Frame(
                    label=name,
                    image=frame,
                    member=member,
                    declared_no=_declared_no_from_member(member),
                )
            )
    finally:
        image.close()
    return frames


def _walk_directory(path: Path) -> List[Path]:
    """Depth-first, natural-sorted walk (files of one dir before descendants)."""
    out: List[Path] = []
    for child in sorted(path.iterdir(), key=lambda p: _natural_key(p.name)):
        if child.is_dir():
            out.extend(_walk_directory(child))
        elif child.suffix.lower() in IMAGE_EXTENSIONS:
            out.append(child)
    return out


def _extract_folder(path: Path, max_pages: int) -> Tuple[List[Frame], List[str], bool]:
    files = _walk_directory(path)
    frames: List[Frame] = []
    skipped: List[str] = []
    truncated = False
    for file in files:
        if len(frames) >= max_pages:
            truncated = True
            break
        rel = str(file.relative_to(path))
        try:
            frames.extend(_extract_image_file(file, rel, member=rel))
        except (IntakeError, UnsupportedFormatError) as exc:
            skipped.append(f"{rel}: {exc}")
    return frames, skipped, truncated


def collect_frames(
    source: Path,
    *,
    kind: str,
    dpi: float = 150.0,
    max_pages: int = 500,
) -> Tuple[List[Frame], List[str], bool]:
    """Turn a validated source into extracted frames (plus skipped/truncated)."""
    if kind == "pdf":
        frames, truncated = _extract_pdf(source, dpi, max_pages)
        return frames, [], truncated
    if kind == "image":
        frames = _extract_image_file(source, source.name)
        return frames, [], False
    if kind == "zip":
        frames, skipped, truncated = _extract_zip(source, max_pages)
        return frames, skipped, truncated
    if kind == "folder":
        frames, skipped, truncated = _extract_folder(source, max_pages)
        return frames, skipped, truncated
    raise UnsupportedFormatError(
        f"unsupported file type for {source} (only PDF, JPG/JPEG, PNG, TIFF, "
        "ZIP or folders are accepted)"
    )