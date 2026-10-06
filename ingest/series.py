"""Group supplied images by acquisition/stack and order using scanner geometry."""
from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path
from statistics import median

from core import cache
from models import Study, StudySeries


def _vector(value, size):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        return None
    try:
        numbers = [float(v) for v in value]
        return numbers if all(math.isfinite(v) for v in numbers) else None
    except (TypeError, ValueError):
        return None


def _position(image):
    orientation = _vector(image.metadata.get("ImageOrientationPatient"), 6)
    position = _vector(image.metadata.get("ImagePositionPatient"), 3)
    if orientation is None or position is None:
        return None
    a, b = orientation[:3], orientation[3:]
    normal = [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
    norm = math.sqrt(sum(v*v for v in normal))
    return sum(n*p for n, p in zip(normal, position)) / norm if norm > 1e-6 else None


def _instance(image):
    try:
        return float(image.metadata.get("InStackPositionNumber", image.metadata.get("InstanceNumber", "inf")))
    except (TypeError, ValueError):
        return float("inf")


def assemble_series(study: Study, *, max_images: int = 0) -> None:
    groups = defaultdict(list)
    object_counts = defaultdict(int)
    for image in study.images:
        object_counts[image.source_path] += 1
    for image in study.images:
        meta = image.metadata
        orientation = _vector(meta.get("ImageOrientationPatient"), 6)
        key = (meta.get("StudyInstanceUID"), meta.get("SeriesInstanceUID") or str(Path(image.source_path).parent),
               image.modality.value, image.body_part,
               tuple(round(v, 3) for v in orientation) if orientation else None,
               meta.get("TemporalPositionIndex", meta.get("TemporalPositionIdentifier")), meta.get("StackID"))
        groups[key].append(image)
    study.series = []
    ordered = []
    for key, images in groups.items():
        series_id = "series-" + cache.make_key(key)[:16]
        positions = [_position(image) for image in images]
        warnings = []
        if all(p is not None for p in positions) and len(images) > 1:
            images.sort(key=lambda image: (_position(image), _instance(image), image.frame_index or 0, image.image_id))
            ordering = "patient-space slice position"
            distances = sorted(set(_position(image) for image in images))
            gaps = [b-a for a, b in zip(distances, distances[1:]) if b-a > 1e-4]
            if len(gaps) >= 2 and max(gaps) > 1.5 * median(gaps):
                warnings.append("Irregular slice spacing: possible missing slices or mixed acquisition; completeness unknown")
            if len(distances) < len(images):
                warnings.append("Repeated slice positions: may contain temporal/echo duplicates; not a validated 3D volume")
        elif any(math.isfinite(_instance(image)) for image in images):
            images.sort(key=lambda image: (_instance(image), image.frame_index or 0, image.image_id))
            ordering = "instance/frame number (geometry incomplete)"
            if len(images) > 1:
                warnings.append("Spatial ordering not verified: orientation/position metadata incomplete")
        else:
            images.sort(key=lambda image: (image.source_path, image.frame_index or 0, image.image_id))
            ordering = "file/frame order (geometry unavailable)"
            if len(images) > 1:
                warnings.append("File order is not anatomical order; acquisition identity/geometry incomplete")
        if not images[0].metadata.get("SeriesInstanceUID"):
            warnings.append("No scanner series identity; grouping is provisional by folder/modality/region")
        series = StudySeries(series_id=series_id, study_uid=key[0], modality=images[0].modality,
                             body_part=images[0].body_part,
                             description=images[0].metadata.get("SeriesDescription", ""),
                             image_ids=[i.image_id for i in images], acquired_count=len(images),
                             ordering=ordering, warnings=warnings)
        for path in dict.fromkeys(image.source_path for image in images):
            example = next(image for image in images if image.source_path == path)
            expected = example.metadata.get("frames", 1)
            if example.is_dicom and object_counts[path] < expected:
                series.coverage = "partial"
                series.warnings.append(f"Source {Path(path).name}: only {object_counts[path]}/{expected} frames decoded; other frames failed")
        for index, image in enumerate(images):
            image.series_id, image.series_order = series_id, index
        study.series.append(series)
        ordered.extend(images)
    study.images = ordered
    if max_images > 0 and len(ordered) > max_images:
        allocations = {series.series_id: 0 for series in study.series}
        slots = max_images
        while slots:
            for series in study.series:
                if allocations[series.series_id] < len(series.image_ids):
                    allocations[series.series_id] += 1
                    slots -= 1
                    if not slots:
                        break
        selected = set()
        for series in study.series:
            count = allocations[series.series_id]
            indices = ([len(series.image_ids)//2] if count == 1 else
                       [round(i*(len(series.image_ids)-1)/(count-1)) for i in range(count)] if count else [])
            series.image_ids = [series.image_ids[i] for i in indices]
            selected.update(series.image_ids)
            if count < series.acquired_count:
                series.coverage = "partial"
                series.warnings.append(f"Sampled {count}/{series.acquired_count} supplied frames for inference")
        study.images = [image for image in ordered if image.image_id in selected]
        study.warnings.append(f"Study contains {len(ordered)} decoded images/frames; sampled {len(study.images)} across ordered series (MAOS_MAX_IMAGES={max_images}).")
