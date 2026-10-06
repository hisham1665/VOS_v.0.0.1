"""Coverage metrics partitioned by evidence kind, modality and body region.

Counts describe pipeline operation, not clinical diagnostic accuracy. Clinical
accuracy requires a separately labeled, clinician-reviewed evaluation dataset.
"""
from __future__ import annotations

from collections import defaultdict


def evaluation_groups(report, study) -> list[dict]:
    from capabilities.image_classification import _region_from_text

    groups = {}

    def group(kind, modality, region):
        key = (kind, modality, region or "unknown")
        if key not in groups:
            groups[key] = {"evidence_kind": kind, "modality": modality, "region": key[2],
                           "supplied": 0, "read": 0, "screened": 0, "quality_limited": 0,
                           "source_ids": [], "scope": "operational coverage, not clinical accuracy"}
        return groups[key]

    for image in study.images:
        screening = report.screening.get(image.image_id, {})
        row = group("direct_image", image.modality.value, screening.get("region") or _region_from_text(image.body_part))
        row["supplied"] += 1
        row["read"] += int(image.image_id in report.image_reads)
        row["screened"] += int(screening.get("status") == "screened")
        row["quality_limited"] += int(report.image_quality.get(image.image_id, image.quality).get("status") in {"limited", "unusable", "unavailable"})
        row["source_ids"].append(image.image_id)
    modalities = {"xray": "X-ray", "ct": "CT", "mri": "MRI", "ultrasound": "Ultrasound"}
    for document in study.documents:
        for page in document.pages:
            ref = f"{document.document_id}#page={page.page_number}"
            kind = report.document_types.get(ref, "unknown")
            imaging = kind in {"radiology_report", *modalities}
            row = group("written_imaging_report" if imaging else "document_page", modalities.get(kind, "unspecified"), "unknown")
            row["supplied"] += 1
            row["read"] += int(ref in report.source_reads)
            row["source_ids"].append(ref)
    return list(groups.values())


def aggregate_reports(reports: list) -> dict:
    """Combine real report coverage without pooling reports with direct images."""
    groups = defaultdict(lambda: {"supplied": 0, "read": 0, "screened": 0, "quality_limited": 0, "runs": 0})
    for report in reports:
        for row in report.evaluation_groups:
            key = (row["evidence_kind"], row["modality"], row["region"])
            group = groups[key]
            group["runs"] += 1
            for field in ("supplied", "read", "screened", "quality_limited"):
                group[field] += row[field]
    return {"runs": len(reports), "degraded_runs": sum(bool(r.degraded) for r in reports),
            "scope": "operational coverage only; no labeled diagnostic-accuracy evaluation performed",
            "groups": [{"evidence_kind": key[0], "modality": key[1], "region": key[2], **row,
                        "read_fraction": row["read"] / row["supplied"] if row["supplied"] else None}
                       for key, row in sorted(groups.items())]}
