"""Batch input discovery (plan Phase 10 -- file discovery / validation).

Turns the ZIP-or-folder input into the batch's pending items. Only real
answer sheets participate: supported extensions are validated (`sniff_kind`),
hidden / macOS-junk members are skipped, and ordering is natural so
``student001..student010`` sorts as people expect.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import List

from aos_v0.exam.intake.formats import IMAGE_EXTENSIONS, _natural_key, sniff_kind
from aos_v0.exam.batch.models import BatchItem

ANSWER_EXTENSIONS = IMAGE_EXTENSIONS | {".pdf"}

_SKIP_MARKERS = ("__MACOSX",)
_HIDDEN_PREFIX = (".")


def _is_supported(name: str) -> bool:
    return Path(name).suffix.lower() in ANSWER_EXTENSIONS


def _is_junk(relative: str) -> bool:
    parts = Path(relative).parts
    if any(part in _SKIP_MARKERS or part.startswith(_HIDDEN_PREFIX) for part in parts):
        return True
    return Path(relative).name.startswith("._")


def _paper_id_of(member: str) -> str:
    return Path(member).stem


def _folder_items(root: Path) -> List[BatchItem]:
    if root.is_file() and _is_supported(str(root)):
        if sniff_kind(root) == "unknown":
            return []
        return [BatchItem(paper_id=_paper_id_of(str(root)), source=str(root))]
    files = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and _is_supported(str(path))
        and not _is_junk(str(path.relative_to(root)))
        and sniff_kind(path) != "unknown"
    ]
    files.sort(key=lambda path: _natural_key(str(path.relative_to(root))))
    return [
        BatchItem(paper_id=_paper_id_of(str(path)), source=str(path))
        for path in files
    ]


def _zip_items(archive: Path) -> List[BatchItem]:
    with zipfile.ZipFile(archive) as zf:
        members = [
            info.filename
            for info in zf.infolist()
            if not info.is_dir()
            and _is_supported(info.filename)
            and not _is_junk(info.filename)
        ]
    members.sort(key=_natural_key)
    return [
        BatchItem(paper_id=_paper_id_of(member), source=member)
        for member in members
    ]


def discover_answer_sheets(source: str | Path) -> List[BatchItem]:
    """Return the pending items for a folder, a ZIP archive, or one file."""
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"batch input not found: {path}")
    if path.is_dir():
        return _folder_items(path)
    if sniff_kind(path) == "zip":
        return _zip_items(path)
    if _is_supported(str(path)):
        return _folder_items(path)
    return []


def resolve_zip_inputs(
    items: List[BatchItem], archive: Path, extract_dir: Path
) -> List[BatchItem]:
    """Materialize ZIP members to disk so every item has a real file path."""
    extract_dir.mkdir(parents=True, exist_ok=True)
    resolved: List[BatchItem] = []
    with zipfile.ZipFile(archive) as zf:
        for item in items:
            member = item.source
            target = extract_dir / member
            try:
                zf.extract(member, extract_dir)
            except (KeyError, zipfile.BadZipFile):
                resolved.append(
                    BatchItem(
                        paper_id=item.paper_id,
                        source=member,
                        status="failed",
                        error=f"zip member unreadable: {member}",
                    )
                )
                continue
            resolved.append(
                BatchItem(paper_id=item.paper_id, source=str(target))
            )
    return resolved