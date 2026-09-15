"""Medical capabilities: the seven run functions registered by Medical AOS.

Every run function follows the existing AOS resource contract --
``run(text, instruction=None) -> str`` -- and is registered as an ordinary
CapabilityManifest in the existing registry (see ``registration.py``). Routing,
admission control, fault recovery and the executor all treat these exactly like
any other resource; nothing here reaches into the kernel.

Input convention: analysis capabilities receive the marker-wrapped manifest
``【MEDICAL:file-manifest】...【/MEDICAL】`` (the workflow embeds it in the job
prompt, and the manager wires analysis nodes downstream of the ingestion node).
Each capability pulls the file list, processes the files it is responsible for,
and emits its own structured block for the patient synthesizer.

Source-traceability rules honored throughout:
  * every finding/lab row/medication carries source file + page;
  * lab status is derived only from the report's own reference range (or the
    report's explicit status) -- reference ranges are never invented;
  * imaging findings are phrased as observations requiring physician/
    radiologist confirmation, never confirmed diagnoses.
"""

from __future__ import annotations

import re
from typing import List, Optional

from aos_v0.medical.clinical import (
    extract_patient_info,
    extract_pdf_pages,
    extract_text,
    find_on_page,
    parse_lab_lines,
)
from aos_v0.medical.hf_medical import (
    medic_gemma_describe,
    medic_lab_ner_lines,
    _ner_lines_to_results,
)
from aos_v0.medical.manifest import (
    CONDITION_NAME,
    IMAGING_NAME,
    LAB_NAME,
    MANIFEST_NAME,
    MEDICATION_NAME,
    OBSERVATION_NAME,
    PATIENT_NAME,
    UNSUPPORTED_NAME,
    FileManifest,
    ImagingFinding,
    LabResult,
    MedicalCondition,
    Medication,
    MedicalObservation,
    PatientProfile,
    UnsupportedFile,
    emit_block,
    folder_hint,
    parse_manifest,
    scan_folder,
)
from aos_v0.providers.hf import ProviderError

# ---------------------------------------------------------------------------
# Folder ingestion
# ---------------------------------------------------------------------------


def _resolve_path(entry: FileManifest, text: str) -> Optional[str]:
    """Resolve a file's local path: entry absolute_path, else folder+relative."""
    if entry.absolute_path:
        return entry.absolute_path
    folder = folder_hint(text)
    if folder and entry.relative_path:
        candidate = f"{folder.rstrip('/')}/{entry.relative_path}"
        from pathlib import Path

        return candidate if Path(candidate).exists() else None
    return None


def medical_folder_ingestion_run(text: str, instruction: Optional[str] = None) -> str:
    """Scan the patient folder and emit the file manifest block.

    Accepts either a raw folder in ``text``, a ``FOLDER: <path>`` hint, or an
    already-embedded manifest (then it just re-emits + refreshes it). Missing
    folder -> explicit empty-manifest block, so downstream nodes degrade
    gracefully instead of erroring.
    """
    hint = folder_hint(text)
    if not hint:
        existing = parse_manifest(text)
        if existing:
            return emit_block(MANIFEST_NAME, [m.model_dump() for m in existing])
        return emit_block(MANIFEST_NAME, []) + "\n(no patient folder found in input)"

    manifests = scan_folder(hint)
    listable = "patient folder found: " + hint + "\n"

    if not manifests:
        unsupported: List[UnsupportedFile] = []
        return (
            listable
            + emit_block(UNSUPPORTED_NAME, [u.model_dump() for u in unsupported])
            + emit_block(MANIFEST_NAME, [])
            + "\n(folder empty)"
        )

    lines = [
        f"Patient folder: {hint}",
        f"{len(manifests)} file(s) discovered by the `medical_folder_ingestion` "
        "capability and emitted as the machine-readable file-manifest block.",
    ]
    body = "\n".join(lines)
    return body + "\n\n" + emit_block(MANIFEST_NAME, [m.model_dump() for m in manifests])


# ---------------------------------------------------------------------------
# Document analysis (generic clinical documents)
# ---------------------------------------------------------------------------


def medical_document_analysis_run(text: str, instruction: Optional[str] = None) -> str:
    """Analyse generic clinical/medical documents: extract page-tagged text,
    patient demographics and observations into structured blocks."""
    entries = parse_manifest(text)
    documents = [e for e in entries if e.category == "document"
                 and e.detected_type in {"medical_document", "clinical_document"}]

    observations: List[MedicalObservation] = []
    profiles: List[PatientProfile] = []
    notes: List[str] = []

    for entry in documents:
        path = _resolve_path(entry, text)
        if not path:
            notes.append(f"[medical_document_analysis] missing file for {entry.filename}")
            continue
        try:
            content = extract_text(path)
        except Exception as exc:  # noqa: BLE001 -- report, keep going
            notes.append(f"[medical_document_analysis] {entry.filename} unreadable: {exc}")
            continue

        page = 1
        for line in content.splitlines():
            line = line.strip()
            # Drop [page N] tags, treat them as traceability carriers only.
            pagen = re.match(r"\[page (\d+)\]", line)
            if pagen:
                page = int(pagen.group(1))
                continue
            if len(line) >= 20 and not line.startswith("http"):
                observations.append(
                    MedicalObservation(
                        observation=" ".join(line.split()),
                        source=entry.relative_path or entry.filename,
                        page=page,
                        source_type="clinical observation (document)",
                    )
                )
                if len(observations) > 120:
                    break
        profile = extract_patient_info(content, source=entry.filename)
        if profile.name or profile.age or profile.sex:
            profiles.append(profile)

    out: List[str] = []
    if profiles:
        out.append(emit_block(PATIENT_NAME, [p.model_dump() for p in profiles]))
    out.append(emit_block(OBSERVATION_NAME, [o.model_dump() for o in observations]))
    if notes:
        out.append("Notes:\n" + "\n".join(notes))
    return "\n\n".join(out) if out else "(no medical documents found to analyse)"


# ---------------------------------------------------------------------------
# Laboratory analysis
# ---------------------------------------------------------------------------


def medical_laboratory_analysis_run(text: str, instruction: Optional[str] = None) -> str:
    """Analyse laboratory reports. NER first, deterministic-pattern parser as
    the in-capability fallback when the NER model is unavailable; never invents
    reference ranges."""
    entries = parse_manifest(text)
    reports = [e for e in entries if e.detected_type == "laboratory_report"]

    results: List[LabResult] = []
    notes: List[str] = []

    for entry in reports:
        path = _resolve_path(entry, text)
        if not path:
            notes.append(f"[medical_laboratory_analysis] missing file for {entry.filename}")
            continue

        # Page-tagged pdfplumber extraction; fall back to raw text on any
        # failure so a corrupt/unusual PDF still yields whatever is readable.
        pages: List[tuple[int, str]] = []
        try:
            pages = extract_pdf_pages(path)
        except Exception as exc:  # noqa: BLE001 -- unreadable report tail
            notes.append(f"[medical_laboratory_analysis] pdfplumber failed for {entry.filename}: {exc}")
        if not pages:
            try:
                raw_text = extract_text(path)
                if raw_text.strip():
                    pages = [(1, raw_text)]
            except Exception as exc:  # noqa: BLE001
                notes.append(f"[medical_laboratory_analysis] {entry.filename} empty: {exc}")
                continue
        if not pages:
            notes.append(f"[medical_laboratory_analysis] {entry.filename} yielded no readable text")
            continue

        ner_used = False
        for page_no, page_text in pages:
            try:
                ner_lines = medic_lab_ner_lines(page_text)
                ner_rows = _ner_lines_to_results(
                    ner_lines, entry.relative_path or entry.filename, page_no
                )
                if ner_rows:
                    results.extend(ner_rows)
                    ner_used = True
            except ProviderError:
                # Model unavailable / unauthenticated -> deterministic fallback.
                ner_rows = []

            if not ner_rows:
                results.extend(parse_lab_lines(page_text, entry.relative_path or entry.filename, page_no))

        if ner_used:
            notes.append(
                f"[medical_laboratory_analysis] {entry.filename}: NER extraction used; "
                "deterministic parser filled the rows the model could not structure."
            )

    # De-duplicate identical (test, source_file, page) rows.
    seen = set()
    unique: List[LabResult] = []
    for r in results:
        key = (r.test.strip().lower(), r.value, r.source_file, r.page)
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)

    out: List[str] = [emit_block(LAB_NAME, [r.model_dump() for r in unique])]
    if notes:
        out.append("Notes:\n" + "\n".join(notes))
    return "\n\n".join(out) if unique else "(no laboratory values extracted)" + ("\n\n" + "\n".join(notes) if notes else "")


# ---------------------------------------------------------------------------
# Image analysis
# ---------------------------------------------------------------------------


def medical_image_analysis_run(text: str, instruction: Optional[str] = None) -> str:
    """Analyse medical images with the configured MedGemma model. Every finding
    is attributed to its source file and flagged as requiring physician/
    radiologist confirmation."""
    entries = parse_manifest(text)
    images = [e for e in entries if e.category == "image"]

    findings: List[ImagingFinding] = []
    notes: List[str] = []

    for entry in images:
        path = _resolve_path(entry, text)
        if not path:
            notes.append(f"[medical_image_analysis] missing file for {entry.filename}")
            continue
        try:
            description = medic_gemma_describe(path)
        except ProviderError as exc:
            notes.append(f"[medical_image_analysis] {entry.filename}: model unavailable: {exc}")
            continue

        snippet = " ".join(description.split())
        findings.append(
            ImagingFinding(
                finding=snippet[:600],
                source=entry.relative_path or entry.filename,
                evidence="Individual finding described by MedGemma from " + (entry.relative_path or entry.filename),
                status="Requires physician/radiologist confirmation.",
            )
        )

    out: List[str] = [emit_block(IMAGING_NAME, [f.model_dump() for f in findings])]
    if notes:
        out.append("Notes:\n" + "\n".join(notes))
    return "\n\n".join(out) if findings else "(no imaging findings extracted)" + ("\n\n" + "\n".join(notes) if notes else "")


# ---------------------------------------------------------------------------
# Prescription analysis
# ---------------------------------------------------------------------------


def _page_iter(text: str):
    """Yield (page_no, content) from page-tagged text produced by extract_text."""
    page_no, buf = 1, []
    for line in text.splitlines():
        m = re.match(r"\[page (\d+)\]", line.strip())
        if m:
            if buf:
                yield page_no, "\n".join(buf)
            page_no = int(m.group(1))
            buf = []
        else:
            buf.append(line)
    if buf:
        yield page_no, "\n".join(buf)


def medical_prescription_analysis_run(text: str, instruction: Optional[str] = None) -> str:
    """Analyse prescription documents: deterministic medication + condition
    extraction, source-grounded to file and page."""
    entries = parse_manifest(text)
    prescriptions = [e for e in entries if e.detected_type == "prescription"]

    meds: List[Medication] = []
    conditions: List[MedicalCondition] = []
    notes: List[str] = []

    for entry in prescriptions:
        path = _resolve_path(entry, text)
        if not path:
            notes.append(f"[medical_prescription_analysis] missing file for {entry.filename}")
            continue
        try:
            raw = extract_text(path)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"[medical_prescription_analysis] {entry.filename} unreadable: {exc}")
            continue

        for page_no, page_content in _page_iter(raw):
            for line in page_content.splitlines():
                line = line.strip()
                low = line.lower()
                if not low or line.startswith(("[", "#")):
                    continue

                # Conditions: explicit diagnosis markers.
                dm = re.search(
                    r"(?:discharge\s+)?diagnosis(?:\s*[:;=\-])?\s*(.{3,120})",
                    line, re.IGNORECASE
                )
                if dm:
                    conditions.append(
                        MedicalCondition(
                            condition=" ".join(dm.group(1).split()).strip(" ."),
                            source=entry.relative_path or entry.filename,
                            page=page_no,
                        )
                    )
                    continue

                # Medications: "Medicine <dose>" or "<dose> Medicine" text forms,
                # e.g. "Amoxicillin 500 mg three times daily". A substance name
                # (capitalized word tokens) must pair with a numeric dose + unit.
                _RANGE_DOSE = r"\d+(?:\.\d+)?\s*(?:mg|mcg|µg|g|ml|cc|units?|iu)\b"
                if re.search(r"\d", line) and re.search(_RANGE_DOSE, low):
                    med_first = re.match(
                        r"^([A-ZÀ-Ý][A-Za-zÀ-ÿ\-]{1,}(?:\s[A-Za-zÀ-ÿ\-]{1,}){0,3})"
                        r"\s+(" + _RANGE_DOSE + r".*)$",
                        line,
                    )
                    dose_first = re.match(
                        r"^(" + _RANGE_DOSE + r")\s+"
                        r"([A-ZÀ-Ý][A-Za-zÀ-ÿ\-]{2,}(?:\s[A-Za-zÀ-ÿ\-]+){0,3}).*$",
                        line,
                    )
                    medicine = dose = tail = ""
                    if med_first:
                        medicine, dose = med_first.group(1), med_first.group(2)
                        tail = line[len(med_first.group(1)) + len(dose):].strip()
                    elif dose_first:
                        dose, medicine = dose_first.group(1), dose_first.group(2)
                        tail = line[len(dose_first.group(0)):].strip()
                    if medicine and not medicine.lower().startswith(
                        ("page", "note", "ref", "prescription", "patient",
                         "diagnosis", "date", "recomm")
                    ):
                        freq = ""
                        fm = re.search(
                            r"(q\d+h|q\d+|daily|bid|tid|qid|once|twice|thrice|od|bd|td)",
                            tail.lower(),
                        )
                        if fm:
                            freq = fm.group(1)
                        meds.append(
                            Medication(
                                medicine=medicine,
                                dose=dose,
                                frequency=freq,
                                duration="",
                                source=entry.relative_path or entry.filename,
                                page=page_no,
                            )
                        )

    out: List[str] = []
    if meds:
        out.append(emit_block(MEDICATION_NAME, [m.model_dump() for m in meds]))
    if conditions:
        out.append(emit_block(CONDITION_NAME, [c.model_dump() for c in conditions]))
    if notes:
        out.append("Notes:\n" + "\n".join(notes))
    return "\n\n".join(out) if out else "(no medications/conditions extracted)"


# ---------------------------------------------------------------------------
# Medical report analysis
# ---------------------------------------------------------------------------


def medical_report_analysis_run(text: str, instruction: Optional[str] = None) -> str:
    """Analyse medical reports / discharge summaries: emit conditions,
    observations and patient demographics as structured blocks."""
    entries = parse_manifest(text)
    reports = [e for e in entries if e.detected_type in {"medical_report", "discharge_summary"}]

    conditions: List[MedicalCondition] = []
    observations: List[MedicalObservation] = []
    profiles: List[PatientProfile] = []
    notes: List[str] = []

    for entry in reports:
        path = _resolve_path(entry, text)
        if not path:
            notes.append(f"[medical_report_analysis] missing file for {entry.filename}")
            continue
        try:
            content = extract_text(path)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"[medical_report_analysis] {entry.filename} unreadable: {exc}")
            continue

        for page_no, page_content in _page_iter(content):
            for line in page_content.splitlines():
                line = line.strip()
                if not line or len(line) < 8:
                    continue
                dm = re.search(
                    r"(?:discharge\s+diagnosis|impression|diagnosis|final\s+diagnosis)"
                    r"(?:\s*[:;=\-])?\s*(.{3,200})",
                    line, re.IGNORECASE,
                )
                if dm:
                    conditions.append(
                        MedicalCondition(
                            condition=" ".join(dm.group(1).split()).strip(" ."),
                            source=entry.relative_path or entry.filename,
                            page=page_no,
                        )
                    )
                elif not line.startswith(("[page", "http", "page ")):
                    observations.append(
                        MedicalObservation(
                            observation=" ".join(line.split())[:200],
                            source=entry.relative_path or entry.filename,
                            page=page_no,
                        )
                    )
        profile = extract_patient_info(content, source=entry.filename)
        if profile.name or profile.age or profile.sex:
            profiles.append(profile)

    out: List[str] = []
    if profiles:
        out.append(emit_block(PATIENT_NAME, [p.model_dump() for p in profiles]))
    if conditions:
        out.append(emit_block(CONDITION_NAME, [c.model_dump() for c in conditions]))
    out.append(emit_block(OBSERVATION_NAME, [o.model_dump() for o in observations]))
    if notes:
        out.append("Notes:\n" + "\n".join(notes))
    return "\n\n".join(out) if out else "(no medical reports found to analyse)"


# ---------------------------------------------------------------------------
# Patient synthesis
# ---------------------------------------------------------------------------


def _parse_kind(text: str, kind: str) -> List[dict]:
    from aos_v0.medical.manifest import parse_blocks

    return parse_blocks(text or "").get(kind, [])


def _fmt_ref(rng: str) -> str:
    return rng.replace("\n", " ").strip()


def _chart_lab_table(rows: List[dict]) -> str:
    lines = [
        "| Test | Value | Unit | Reference Range | Status | Source | Page |",
        "|------|-------|------|-----------------|--------|--------|------|",
    ]
    for r in rows:
        lines.append(
            f"| {r.get('test','')} | {r.get('value','')} | {r.get('unit','')} "
            f"| {_fmt_ref(r.get('reference_range',''))} | {r.get('status','UNKNOWN')} "
            f"| {r.get('source_file','')} | {r.get('page') or ''} |"
        )
    return "\n".join(lines)


def _chart_findings(findings: List[dict]) -> str:
    lines = [
        "| Finding | Source | Confirmation Status |",
        "|---------|--------|---------------------|",
    ]
    for f in findings:
        snippet = f.get("finding", "") or ""
        snippet = snippet if len(snippet) <= 120 else snippet[:117] + "..."
        lines.append(f"| {snippet} | {f.get('source','')} | {f.get('status','')} |")
    return "\n".join(lines)


def _chart_table(title: str, headers: List[str], rows: List[dict], cols: List[str]) -> str:
    lines = [f"**{title}**", "", "| " + " | ".join(headers) + " |",
             "|" + "---|" * len(headers)]
    for r in rows:
        cells = [str(r.get(c, "")) for c in cols]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def medical_patient_synthesis_run(text: str, instruction: Optional[str] = None) -> str:
    """Merge every structured block emitted by the analysis capabilities into a
    single source-grounded patient chart. Deterministic assembly -- no LLM call
    here, so the chart is reproducible from the same inputs."""
    labs = _parse_kind(text, LAB_NAME)
    findings = _parse_kind(text, IMAGING_NAME)
    meds = _parse_kind(text, MEDICATION_NAME)
    conds = _parse_kind(text, CONDITION_NAME)
    obs = _parse_kind(text, OBSERVATION_NAME)
    profiles = _parse_kind(text, PATIENT_NAME)
    unsupported = _parse_kind(text, UNSUPPORTED_NAME)

    sections: List[str] = []

    header = ["# Patient Chart (Medical AOS)"]
    header.append("")
    header.append("> Every value below is grounded in the source files listed. "
                  "This chart is a structured aggregation of the source documents; "
                  "it is NOT a medical opinion and does not replace clinical review.")
    sections.append("\n".join(header))

    if profiles:
        sections.append("## Patient Profile")
        for p in profiles[:3]:
            bits = {k: v for k, v in p.items() if v}
            sections.append("  - " + "; ".join(f"{k}: {v}" for k, v in bits.items()))
        sections.append("")

    sections.append("## Diagnostic Laboratory Values")
    sections.append(_chart_lab_table(labs) if labs else "No laboratory values were extracted.")
    sections.append("")

    sections.append("## Imaging Findings (AI-assisted) — confirmation required")
    sections.append(_chart_findings(findings) if findings else "No imaging findings were extracted.")
    sections.append("")

    if meds:
        sections.append(_chart_table(
            "Medications from Prescriptions",
            ["Medicine", "Dose / Route", "Frequency", "Duration", "Source", "Page"],
            meds, ["medicine", "dose", "frequency", "duration", "source", "page"],
        ))
        sections.append("")

    if conds:
        sections.append(_chart_table(
            "Diagnoses / Conditions (source-reported)",
            ["Condition", "Source", "Page"],
            conds, ["condition", "source", "page"],
        ))
        sections.append("")

    if obs:
        sections.append("## Clinical Observations (source-extracted)")
        for o in obs[:40]:
            src = o.get("source", "")
            pg = o.get("page")
            sections.append(f"  - {o.get('observation','')}  *(source: {src}"
                            + (f", page {pg}" if pg else "") + ")*")
        sections.append("")

    if unsupported:
        sections.append("## Unsupported / Unprocessed Files")
        for u in unsupported[:20]:
            sections.append(f"  - {u.get('filename','')}: {u.get('reason','')}")
        sections.append("")

    sections.append("---")
    sections.append("*Chart generated deterministically from the medical "
                    "capability outputs. Review with a qualified clinician.*")

    return "\n".join(sections)