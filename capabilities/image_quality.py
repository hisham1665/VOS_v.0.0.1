"""Technical image checks and explicit acquisition coverage before inference."""
from __future__ import annotations

import json

from capabilities.registry import CapabilityRequest, CapabilityResult, register
from core.routing import CapabilityDNA


@register("image_quality", "Check technical pixel quality and supplied series coverage.",
          dna=CapabilityDNA("image_quality", skills=("check_image_quality",), inputs=("images",),
                            outputs=("image_quality",), domain="medical", cost_per_call=0,
                            expected_latency_s=0.1, memory_gb=0.05))
def run(request: CapabilityRequest) -> CapabilityResult:
    from ingest.preprocess import assess_pixel_quality, load_pil

    quality = {}
    for image in request.images():
        info = dict(image.quality)
        if not info:
            try:
                info = assess_pixel_quality(load_pil(image.path_for_model))
            except Exception as exc:
                info = {"status": "unavailable", "issues": [f"Pixel quality check unavailable: {exc}"]}
        quality[image.image_id] = {**info, "series_id": image.series_id,
                                   "frame_index": image.frame_index, "series_order": image.series_order}
    series = [item.model_dump(mode="json") for item in request.study.series] if request.study else []
    return CapabilityResult(text="Technical image quality and supplied series context:\n" +
                            json.dumps({"images": quality, "series": series}, ensure_ascii=False),
                            artifacts={"image_quality": quality, "series_context": series})
