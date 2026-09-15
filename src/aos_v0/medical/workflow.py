"""Medical workflow front-end: pre-scan + job-prompt builder.

The Medical AOS workflow does NOT install its own pipeline. It performs one
non-kernel step up front -- a deterministic folder scan that yields a
`file-manifest` -- and then *passes that manifest into the hands of the existing
orchestration*: the manifest is embedded in the job prompt (marker-wrapped), the
existing ManagerAgent plans the DAG, the existing Capability DNA extractor
decides flags, and the existing executor routes/executes/recoveries. The
manifest is carried in the job prompt precisely so any node (root analysis
nodes receive `graph.job`) can extract it without any core changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from aos_v0.medical.manifest import (
    FileManifest,
    emit_block,
    scan_folder,
)

MEDICAL_WORKFLOW_DIRECTIVE = """\
MEDICAL WORKFLOW (requested via --medical-folder): plan the analysis of the \
patient folder file(s) listed in the manifest below using the medical \
capabilities ONLY:

- 'medical_folder_ingestion': exactly ONE root node that lists every file in \
the folder.
- For each category present in the manifest, create analysis node(s) that \
depend on the ingestion node, one node per distinct analysis category (do not \
merge categories): 'medical_image_analysis' for image files, \
'medical_laboratory_analysis' for laboratory reports, \
'medical_prescription_analysis' for prescriptions, \
'medical_report_analysis' for medical reports / discharge summaries, and \
'medical_document_analysis' for other clinical documents.
- 'medical_patient_synthesis': exactly ONE node depending on ALL analysis \
nodes; it merges the structured results into one source-grounded patient chart.

The manifest block below is authoritative input: every analysis node will \
receive it. Do not invent files that are not listed.
"""


def build_medical_job(
    folder: str | Path,
    user_prompt: Optional[str] = None,
) -> tuple[str, list[FileManifest]]:
    """Scan `folder` and build the job prompt for the existing orchestrator.

    Returns (job_prompt, manifests). The prompt embeds:
      * the user's own prose (when given),
      * `FOLDER: <path>` for ingestion,
      * the medical workflow directive,
      * the deterministic【MEDICAL:file-manifest】 block.
    """
    folder = str(Path(folder).expanduser().resolve())
    manifests = scan_folder(folder)

    parts: list[str] = []
    if user_prompt and user_prompt.strip():
        parts.append(user_prompt.strip())

    parts.append(f"FOLDER: {folder}")
    parts.append(MEDICAL_WORKFLOW_DIRECTIVE)

    if not manifests:
        parts.append("(the patient folder is empty: no files were found to analyse)")
        parts.append(emit_block("file-manifest", []))
    else:
        summary = "Patient folder pre-scan summary (file count by category):\n"
        counts: dict[str, int] = {}
        for m in manifests:
            counts[m.detected_type] = counts.get(m.detected_type, 0) + 1
        summary += "\n".join(f"  - {k}: {v}" for k, v in sorted(counts.items()))
        parts.append(summary)
        parts.append(emit_block("file-manifest", [m.model_dump() for m in manifests]))

    return "\n\n".join(parts), manifests


def folder_from_prompt(prompt: str) -> Optional[str]:
    """Resolve the `FOLDER: <path>` hint a medical job carries, if any."""
    from aos_v0.medical.manifest import folder_hint

    return folder_hint(prompt)