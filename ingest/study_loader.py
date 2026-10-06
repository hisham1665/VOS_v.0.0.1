"""Turn a study folder into a validated, provenance-preserving Study.

Images, DICOM, PDFs and common office/text exports are ingested independently.
A bad input is recorded and never prevents other files from being analysed.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import re
from pathlib import Path

from config import (
    RUNTIME,
    SUPPORTED_DICOM_SUFFIXES,
    SUPPORTED_DOCUMENT_SUFFIXES,
    SUPPORTED_IMAGE_SUFFIXES,
)
from core.errors import StudyError
from core.logging_utils import get_logger, timed
from ingest.dicom_utils import is_dicom
from ingest.document_loader import load_document
from ingest.preprocess import prepare_dicom_frames, prepare_image
from ingest.series import assemble_series
from models import Modality, PatientContext, Study, StudyDocument, StudyImage

_log = get_logger("study")
_IGNORE_NAMES = {"thumbs.db", ".ds_store", "desktop.ini"}
_CONTEXT_KEYS = {
    "patient_id", "age", "sex", "clinical_history", "history",
    "question", "referring_question", "prior_studies",
}
_MODALITY_MAP = {
    "CR": Modality.XRAY, "DX": Modality.XRAY, "RG": Modality.XRAY,
    "CT": Modality.CT, "MR": Modality.MRI, "US": Modality.ULTRASOUND,
    "MG": Modality.MAMMOGRAPHY, "PT": Modality.PET, "SM": Modality.PATHOLOGY,
}
# Hints are matched as tokens, not arbitrary substrings: "doctor.pdf" must
# not be classified as CT merely because it contains the letters "ct". Token
# boundaries are any non-alphanumeric character -- ``\b`` treats "_" as a word
# character, so "ct.jpg" and "chest_ct.png" used to slip through as Unknown.
_TOKEN = r"(?:^|[^a-z0-9]){}(?:[^a-z0-9]|$)"
# Modality words first; "chest" names a region, so it is only a weak X-ray
# fallback when no modality word is present ("chest_ct.png" is a CT).
_FILENAME_HINTS = (
    (_TOKEN.format(r"(?:x[-_ ]?ray|cxr|radiograph|xr|dx|cr)"), Modality.XRAY),
    (_TOKEN.format(r"(?:ct|cect|ncct|cta)"), Modality.CT),
    (_TOKEN.format(r"(?:mri|mr|t1|t2|flair|dwi)"), Modality.MRI),
    (_TOKEN.format(r"(?:ultrasound|usg|sono|sonogram|us)"), Modality.ULTRASOUND),
    (_TOKEN.format(r"(?:mammo|mammogram|mg)"), Modality.MAMMOGRAPHY),
    (_TOKEN.format(r"pet"), Modality.PET),
    (_TOKEN.format(r"(?:histo|path)"), Modality.PATHOLOGY),
    (_TOKEN.format(r"chest"), Modality.XRAY),
)


def load_study(
    folder: str | Path,
    *,
    clinical_history: str | None = None,
    referring_question: str | None = None,
) -> Study:
    root = Path(folder).expanduser()
    if not root.exists():
        raise StudyError(f"Study folder does not exist: {root}")

    if root.is_file():
        root, candidates = root.parent, [root]
    else:
        root = root.resolve()
        candidates = []
        for candidate in sorted(root.rglob("*")):
            if not candidate.is_file():
                continue
            try:
                relative = candidate.relative_to(root)
                if any(part.startswith(".") for part in relative.parts):
                    continue
                # Do not follow a symlink to a file outside the supplied study.
                if candidate.is_symlink() and not candidate.resolve().is_relative_to(root):
                    continue
            except (OSError, ValueError):
                continue
            candidates.append(candidate)

    if not candidates:
        raise StudyError(f"Study folder is empty: {root}")

    study = Study(study_id=_study_id(root), source_dir=str(root.resolve()))
    study.patient = PatientContext(
        clinical_history=clinical_history,
        referring_question=referring_question,
    )

    image_files: list[Path] = []
    document_files: list[Path] = []
    for path in candidates:
        if path.name.lower() in _IGNORE_NAMES or path.name.startswith("."):
            continue
        suffix = path.suffix.lower()
        if suffix == ".json" and path.name.lower() in {"patient.json", "patient_context.json"}:
            if _absorb_patient_sidecar(path, study):
                continue
            # Invalid/extended sidecars are retained as analyzable documents.
            document_files.append(path)
        elif suffix in SUPPORTED_DOCUMENT_SUFFIXES:
            document_files.append(path)
        elif suffix in SUPPORTED_IMAGE_SUFFIXES or suffix in SUPPORTED_DICOM_SUFFIXES:
            image_files.append(path)
        else:
            study.skipped_files.append(f"{path.name}: unsupported extension '{suffix}'")

    if not image_files and not document_files:
        raise StudyError(
            f"No readable images or documents found in {root}. Supported images: "
            f"{sorted(SUPPORTED_IMAGE_SUFFIXES)}; DICOM; documents: "
            f"{sorted(SUPPORTED_DOCUMENT_SUFFIXES)}. Skipped {len(study.skipped_files)} file(s)."
        )

    if image_files:
        with timed(_log, f"decoding {len(image_files)} image(s)"):
            workers = max(1, min(RUNTIME.max_workers, len(image_files)))
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                results = [item for batch in pool.map(lambda p: _load_file(p, root), image_files) for item in batch]
        _add_images(study, results)
        assemble_series(study, max_images=RUNTIME.max_images_per_study)

    if document_files:
        with timed(_log, f"extracting {len(document_files)} document(s)"):
            workers = max(1, min(RUNTIME.max_workers, len(document_files)))
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(lambda p: _load_document_one(p, root), document_files))
        _add_documents(study, results)

    if not study.images and not study.documents:
        raise StudyError(
            f"Every file in {root} failed to decode or extract:\n  "
            + "\n  ".join(study.skipped_files[:10])
        )

    _log.info(
        "loaded study %s: %d image(s), %d document(s), %d skipped, modalities=%s",
        study.study_id, len(study.images), len(study.documents),
        len(study.skipped_files), [m.value for m in study.modalities],
    )
    return study


def _image_from_prepared(path, root, prepared, info, *, dicom=False):
    metadata = info.get("dicom_metadata", {}) or {}
    width, height = info.get("prepared_size", [0, 0])
    index = metadata.get("frame_selected")
    image_id = _image_id(path, root)
    if metadata.get("frames", 1) > 1:
        image_id += f"#frame={index + 1}"
    return StudyImage(
        image_id=image_id, source_path=str(path.resolve()), prepared_path=str(prepared), content_hash=info["content_hash"],
        prepared_hash=info.get("prepared_hash"),
        modality=_infer_modality(path, metadata), body_part=metadata.get("BodyPartExamined"), view=metadata.get("ViewPosition"),
        width=width, height=height, is_dicom=dicom, metadata=metadata, frame_index=index,
        quality=info.get("quality", {}), warnings=list(info.get("warnings", [])),
    )


def _load_file(path, root):
    dicom = path.suffix.lower() in {".dcm", ".dicom", ".ima"} or (not path.suffix and is_dicom(path))
    if not dicom:
        return [_load_one(path, root)]
    results = []
    try:
        if path.stat().st_size > RUNTIME.max_file_mb * 1024 * 1024:
            raise ValueError(f"file exceeds MAOS_MAX_FILE_MB={RUNTIME.max_file_mb}")
        for prepared, info in prepare_dicom_frames(path):
            if prepared is None:
                index = info.get("dicom_metadata", {}).get("frame_selected", 0)
                results.append((path, None, f"frame {index + 1}: {info['error']}"))
            else:
                results.append((path, _image_from_prepared(path, root, prepared, info, dicom=True), None))
    except Exception as exc:
        results.append((path, None, f"{type(exc).__name__}: {exc}"))
    return results


def _load_one(path: Path, root: Path) -> tuple[Path, StudyImage | None, str | None]:
    try:
        if path.stat().st_size > RUNTIME.max_file_mb * 1024 * 1024:
            raise ValueError(f"file exceeds MAOS_MAX_FILE_MB={RUNTIME.max_file_mb}")
        suffix = path.suffix.lower()
        dicom = suffix in {".dcm", ".dicom", ".ima"} or (suffix == "" and is_dicom(path))
        prepared, info = prepare_image(path, is_dicom=dicom)
        image = _image_from_prepared(path, root, prepared, info, dicom=dicom)
        return path, image, None
    except Exception as exc:
        _log.warning("could not read %s: %s", path.name, exc)
        return path, None, f"{type(exc).__name__}: {exc}"


def _load_document_one(path: Path, root: Path) -> tuple[Path, StudyDocument | None, str | None]:
    try:
        document = load_document(path, root=root)
        return path, document, None
    except Exception as exc:  # defensive per-file boundary
        _log.warning("could not extract %s: %s", path.name, exc)
        return path, None, f"{type(exc).__name__}: {exc}"


def _add_images(study: Study, results) -> None:
    seen: dict[str, str] = {}
    for path, image, error in results:
        if error:
            study.skipped_files.append(f"{path.name}: {error}")
            continue
        assert image is not None
        if image.content_hash in seen:
            study.skipped_files.append(f"{path.name}: duplicate of {seen[image.content_hash]}")
            continue
        seen[image.content_hash] = path.name
        study.images.append(image)


def _add_documents(study: Study, results) -> None:
    seen: dict[str, str] = {}
    for path, document, error in results:
        if error:
            study.skipped_files.append(f"{path.name}: {error}")
            continue
        assert document is not None
        if document.content_hash and document.content_hash in seen:
            study.skipped_files.append(f"{path.name}: duplicate of {seen[document.content_hash]}")
            continue
        if document.content_hash:
            seen[document.content_hash] = path.name
        study.documents.append(document)
        if not document.pages:
            study.warnings.append(
                f"Document {document.document_id} produced no readable pages; see document warnings."
            )


def _infer_modality(path: Path, metadata: dict) -> Modality:
    code = str(metadata.get("Modality", "")).upper().strip()
    if code in _MODALITY_MAP:
        return _MODALITY_MAP[code]
    haystack = f"{path.parent.name}/{path.name}".lower().replace("\\", "/")
    for pattern, modality in _FILENAME_HINTS:
        if re.search(pattern, haystack):
            return modality
    return Modality.UNKNOWN


def _absorb_patient_sidecar(path: Path, study: Study) -> bool:
    """Consume only a pure patient-context sidecar; analyze extra JSON data."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    for key, target in (
        ("patient_id", "patient_id"), ("age", "age"), ("sex", "sex"),
        ("clinical_history", "clinical_history"), ("history", "clinical_history"),
        ("question", "referring_question"), ("referring_question", "referring_question"),
    ):
        value = data.get(key)
        if value and not getattr(study.patient, target):
            setattr(study.patient, target, str(value))
    priors = data.get("prior_studies")
    if isinstance(priors, list):
        study.patient.prior_studies.extend(str(item) for item in priors)
    # Extra fields are clinically meaningful input and must remain an analyzed
    # document even though the standard context keys were absorbed above.
    return not (set(data) - _CONTEXT_KEYS)


def _study_id(root: Path) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in root.name).strip("-") or "study"
    digest = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:8]
    return f"{slug[:32]}-{digest}"


def _image_id(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = Path(path.name)
    return "/".join(relative.parts)
