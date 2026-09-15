"""Medical data structures + deterministic folder ingestion/classification.

The Folder Ingestion capability uses this module to recursively scan a supplied
folder, classify every file (MIME + extension + filename keywords), and build a
`FileManifest` for each discovered file. The manifests are passed into the
existing AOS orchestration system as text (marker-wrapped JSON) so the Manager /
DNA extractor decide which medical capabilities the workflow requires; nothing
here invents a parallel orchestration path.

All cross-capability hand-offs use the `【MEDICAL:<kind>】...【/MEDICAL】` block
format so downstream nodes (and the patient-chart builder) can parse structured
results deterministically from labeled text.
"""

from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Classification vocabulary
# ---------------------------------------------------------------------------

# Medical image (radiology) sub-types, ordered -- first keyword hit wins.
_RADIOLOGY_KEYWORDS = [
    ("xray", "xray"),
    ("x-ray", "xray"),
    ("radiograph", "xray"),
    ("ct scan", "ct_scan"),
    ("cat scan", "ct_scan"),
    (" ct", "ct_scan"),
    ("mri", "mri"),
    ("ultrasound", "scan"),
    ("usg", "scan"),
    ("echo", "scan"),
    ("scan", "scan"),
]

# Document sub-types, ordered -- first keyword hit wins.
_DOCUMENT_KEYWORDS = [
    ("prescri", "prescription"),
    ("discharge", "discharge_summary"),
    ("blood", "laboratory_report"),
    ("urine", "laboratory_report"),
    ("hemogram", "laboratory_report"),
    ("cbc", "laboratory_report"),
    ("lipid", "laboratory_report"),
    ("glucose", "laboratory_report"),
    ("glycated", "laboratory_report"),
    ("thyroid", "laboratory_report"),
    ("lft", "laboratory_report"),
    ("rft", "laboratory_report"),
    ("pathology", "laboratory_report"),
    ("lab", "laboratory_report"),
    ("mri report", "medical_report"),
    ("xray report", "medical_report"),
    ("x-ray report", "medical_report"),
    ("medical report", "medical_report"),
    ("clinical summary", "medical_report"),
    ("patient summary", "medical_report"),
    ("medical summary", "medical_report"),
    ("report", "medical_report"),
    ("history", "clinical_document"),
    ("clinical notes", "clinical_document"),
    ("note", "clinical_document"),
    ("referral", "clinical_document"),
    ("document", "clinical_document"),
]

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff",
               ".tif", ".dicom", ".dcm", ".nii", ".nii.gz"}
_DOCUMENT_EXTS = {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md",
                  ".csv", ".html", ".xml", ".json"}
_UNSUPPORTED_EXTS = {".exe", ".zip", ".tar", ".gz", ".7z", ".dll", ".so",
                     ".py", ".js", ".bin"}

# Marker-based structured hand-off blocks.
BLOCK_BEGIN = "【MEDICAL:{kind}】"
BLOCK_END = "【/MEDICAL】"
# Marker-based structured hand-off blocks. Parsing is deliberately scanner-
# based (not a single regex): we iterate over ``【MEDICAL:<kind>】`` occurrences,
# take everything up to the next ``【/MEDICAL】`` as the candidate payload, and
# validate it as JSON. A prose mention of the marker (e.g. in surrounding
# instructions) yields no parseable JSON and is skipped rather than consuming a
# real block.
_BLOCK_BEGIN_RE = re.compile(r"【MEDICAL:([a-zA-Z_\-]+)】")
_BLOCK_END_RE = re.compile(r"【/MEDICAL】")

MANIFEST_NAME = "file-manifest"
LAB_NAME = "lab-results"
IMAGING_NAME = "imaging-findings"
MEDICATION_NAME = "medications"
CONDITION_NAME = "conditions"
PATIENT_NAME = "patient-profile"
OBSERVATION_NAME = "observations"
UNSUPPORTED_NAME = "unsupported-files"


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


class FileManifest(BaseModel):
    """One discovered file inside a patient folder (Medical AOS section 3)."""

    filename: str
    relative_path: str
    extension: str = ""
    mime_type: str = "application/octet-stream"
    file_size: int = 0
    detected_type: str = "unknown"
    processing_status: str = "ready"
    category: str = "unknown"  # image | document | text | unsupported
    absolute_path: str = ""
    page_count: Optional[int] = None
    diagnosis: Optional[str] = None  # optional set by classification keyword
    notes: str = ""


class LabResult(BaseModel):
    """One structured laboratory row (Medical AOS section 7)."""

    test: str = ""
    value: str = ""
    unit: str = ""
    reference_range: str = ""
    status: str = "UNKNOWN"  # HIGH | LOW | NORMAL | CRITICAL | UNKNOWN
    source_file: str = ""
    page: Optional[int] = None
    source_type: str = "extracted laboratory value"
    note: str = ""


class ImagingFinding(BaseModel):
    """One AI-interpretation finding from a medical image (section 11)."""

    finding: str = ""
    source: str = ""
    evidence: str = ""
    status: str = "Requires physician/radiologist confirmation."


class Medication(BaseModel):
    """One prescribed/documented medicine."""

    medicine: str = ""
    dose: str = ""
    frequency: str = ""
    duration: str = ""
    source: str = ""
    page: Optional[int] = None
    source_type: str = "Documented prescription"
    note: str = ""


class MedicalCondition(BaseModel):
    """One documented diagnosis / condition from a source document."""

    condition: str = ""
    source: str = ""
    page: Optional[int] = None
    source_type: str = "Documented diagnosis"
    note: str = ""


class MedicalObservation(BaseModel):
    """One extracted clinical observation / note."""

    observation: str = ""
    source: str = ""
    page: Optional[int] = None
    source_type: str = "clinical observation"


class PatientProfile(BaseModel):
    """Demographic fields found in the documents, when present."""

    name: str = ""
    age: str = ""
    sex: str = ""
    notes: str = ""


class UnsupportedFile(BaseModel):
    """A file that could not be processed (unsupported type / corrupt)."""

    filename: str = ""
    relative_path: str = ""
    reason: str = ""


# ---------------------------------------------------------------------------
# Folder ingestion (deterministic classification)
# ---------------------------------------------------------------------------


def classify_file(path: Path, detected_type: str = "") -> dict:
    """Classify one file: category + detected_type from MIME/extension/keywords."""
    ext = path.suffix.lower() or ""

    if ext in _IMAGE_EXTS:
        category = "image"
    elif ext in _DOCUMENT_EXTS:
        category = "document"
    elif ext in _UNSUPPORTED_EXTS:
        category = "unsupported"
    else:
        category = "unsupported"

    mime, _ = mimetypes.guess_type(path.name)
    mime = mime or "application/octet-stream"

    name_l = path.name.lower()

    # Radiology sub-typing first (images).
    if category == "image":
        for keyword, label in _RADIOLOGY_KEYWORDS:
            if keyword in name_l:
                return {
                    "category": category,
                    "detected_type": label,
                    "mime_type": mime,
                    "extension": ext,
                    "diagnosis": None,
                }
        return {
            "category": category,
            "detected_type": "medical_image",
            "mime_type": mime,
            "extension": ext,
            "diagnosis": None,
        }

    if category == "document":
        for keyword, label in _DOCUMENT_KEYWORDS:
            if keyword in name_l:
                return {
                    "category": category,
                    "detected_type": "laboratory_report" if label == "laboratory_report" else label,
                    "mime_type": mime,
                    "extension": ext,
                    "diagnosis": None,
                }
        return {
            "category": category,
            "detected_type": "medical_document",
            "mime_type": mime,
            "extension": ext,
            "diagnosis": None,
        }

    return {
        "category": category,
        "detected_type": "unsupported",
        "mime_type": mime,
        "extension": ext,
        "diagnosis": None,
    }


def scan_folder(folder: str | Path) -> List[FileManifest]:
    """Recursively scan `folder` and build a FileManifest per file.

    Skips hidden files and directories (dot-prefixed), which keeps stray cache/
    OS files out of the patient chart. Returns an empty list if the folder does
    not exist.
    """
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        return []

    manifests: List[FileManifest] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        try:
            size = path.stat().st_size
            tax = classify_file(path)
            try:
                page_count = _count_pdf_pages(path) if tax["extension"] == ".pdf" else None
            except Exception:  # noqa: BLE001 -- corrupt PDF -> keep manifest entry
                page_count = None
            manifests.append(
                FileManifest(
                    filename=path.name,
                    relative_path=str(path.relative_to(root)),
                    extension=tax["extension"],
                    mime_type=tax["mime_type"],
                    file_size=size,
                    detected_type=tax["detected_type"],
                    processing_status="ready",
                    category=tax["category"],
                    absolute_path=str(path),
                    page_count=page_count,
                    diagnosis=tax.get("diagnosis"),
                )
            )
        except OSError as exc:
            manifests.append(
                FileManifest(
                    filename=path.name,
                    relative_path=str(path.relative_to(root)),
                    extension=path.suffix.lower(),
                    detected_type="unsupported",
                    processing_status="error",
                    category="unsupported",
                    absolute_path=str(path),
                    notes=f"unreadable: {exc}",
                )
            )
    return manifests


def _count_pdf_pages(path: Path) -> Optional[int]:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return len(pdf.pages)


# ---------------------------------------------------------------------------
# Marker-wrapped structured hand-off blocks
# ---------------------------------------------------------------------------


def emit_block(kind: str, payload) -> str:
    """Wrap structured data in the marker format used across capabilities."""
    if isinstance(payload, BaseModel):
        payload = payload.model_dump()
    if isinstance(payload, list):
        payload = [p.model_dump() if isinstance(p, BaseModel) else p for p in payload]
    return f"{BLOCK_BEGIN.format(kind=kind)}{json.dumps(payload)}{BLOCK_END}"


def parse_blocks(text: str) -> Dict[str, list]:
    """Extract every 【MEDICAL:<kind>】block from free text.

    Scanner-based: each begin marker yields a candidate payload spanning to the
    *next* end marker; anything that does not json-loads as a list or object is
    treated as prose and skipped, and scanning resumes after the begin marker so
    real blocks are never consumed by a false start.
    """
    text = text or ""
    found: Dict[str, list] = {}
    cursor = 0
    while True:
        begin = _BLOCK_BEGIN_RE.search(text, cursor)
        if not begin:
            break
        kind = begin.group(1)
        end = _BLOCK_END_RE.search(text, begin.end())
        if not end:
            break
        raw = text[begin.end(): end.start()].strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            cursor = begin.end()
            continue
        if isinstance(data, list):
            found.setdefault(kind, []).extend(data)
        elif isinstance(data, dict):
            found.setdefault(kind, []).append(data)
        cursor = end.end()
    return found


def parse_manifest(text: str) -> List[FileManifest]:
    """Parse FileManifest list from any text carrying the manifest block."""
    blocks = parse_blocks(text)
    raw = blocks.get(MANIFEST_NAME, [])
    entries: List[FileManifest] = []
    for item in raw:
        try:
            entries.append(FileManifest.model_validate(item))
        except Exception:  # noqa: BLE001 -- one malformed entry must not kill the run
            continue
    return entries


def folder_hint(text: str) -> Optional[str]:
    """Pull the patient folder path out of a job prompt / node input.

    The workflow embeds `FOLDER: <path>` in the job prompt; the ingestion
    capability also accepts a raw directory path directly.
    """
    m = re.search(r"FOLDER:\s*(.+)", text or "")
    if m:
        candidate = m.group(1).strip().splitlines()[0].strip()
        return candidate
    stripped = (text or "").strip()
    if not stripped:
        return None
    if "\n" not in stripped and Path(stripped).is_dir():
        return stripped
    return None