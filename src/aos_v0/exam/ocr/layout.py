"""Layout model adapters and rule-based layout analysis (plan Phase 4).

The plan's layout step takes OCR text + bounding boxes and produces labelled
regions: text, question, answer, tables, diagrams. Two adapters sit here:

  * **DeclaredLayoutAdapter** -- the Phase-1 selected layout model
    (LayoutLMv3), declared-only until a transport exists (gap G3);
  * **RuleLayoutAdapter** -- a runnable, deterministic, pure-Python analyzer.
    It uses geometry + block text as the *layout model*: block types from the
    OCR adapter, question-number anchors ("Q1.", "3)", "Question 2"), row
    clustering for tables, and gap analysis for text regions. Deterministic so
    Phase 5 (sheet structuring) can consume and test against it.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import List, Optional

from PIL import Image

from aos_v0.exam.model_selection import selection_for_capability
from aos_v0.exam.ocr.models import (
    LayoutRegion,
    OcrBlock,
    OcrBlockType,
    OcrUnavailableError,
    RegionType,
)

_QUESTION_ANCHOR = re.compile(
    r"^\s*q(?:uestion)?\s*#?\s*\d{1,3}\b"
    r"|^\s*\d{1,3}\s*[.)]\s*(?=\S)"
    r"|^\s*q\s*\d{1,3}\s*[-:]\s*\S",
    re.IGNORECASE,
)
_TABLE_ROW_TOLERANCE = 8  # px of y-center spread allowed in one cell row


class LayoutAdapter(ABC):
    """Uniform layout-analysis surface."""

    adapter_id: str = "base"
    model_name: str = ""
    model_repo: str = ""
    description: str = ""

    @abstractmethod
    def analyze(
        self, image: Image.Image, blocks: List[OcrBlock]
    ) -> List[LayoutRegion]:
        """Label page regions from the OCR blocks (may raise OcrError)."""


class DeclaredLayoutAdapter(LayoutAdapter):
    """LayoutLMv3 (primary DOCUMENT_LAYOUT) -- declared adapter."""

    def __init__(self):
        selection = selection_for_capability("DOCUMENT_LAYOUT")
        primary = selection.primary if selection is not None else None
        self.adapter_id = "layoutlmv3_base"
        self.model_name = primary.name if primary else "LayoutLMv3 base"
        self.model_repo = primary.hf_repo if primary else "microsoft/layoutlmv3-base"
        self.description = (
            f"declared adapter for '{self.model_name}' ({self.model_repo}); "
            "its layout transport is not wired (gap G3)"
        )

    def analyze(
        self, image: Image.Image, blocks: List[OcrBlock]
    ) -> List[LayoutRegion]:
        raise OcrUnavailableError(
            f"execution transport for '{self.model_name}' is not wired "
            f"(integration-spec gap G3; Phase 8)"
        )


class RuleLayoutAdapter(LayoutAdapter):
    """Deterministic rule-based layout analyzer (runnable, no ML)."""

    adapter_id = "rule_layout"
    model_name = "rule-based layout analyzer (no ML)"
    model_repo = "local"
    description = (
        "Deterministic layout analysis: block-type mapping, question-anchor "
        "segmentation, row-cluster table detection and text-gap regions."
    )

    def analyze(
        self, image: Image.Image, blocks: List[OcrBlock]
    ) -> List[LayoutRegion]:
        w, h = image.size if image is not None else (0, 0)
        if not blocks:
            return []

        # 1. Adapter-typed blocks map directly to regions.
        direct: dict = {
            OcrBlockType.HEADER: RegionType.HEADER,
            OcrBlockType.TABLE: RegionType.TABLE,
            OcrBlockType.DIAGRAM: RegionType.DIAGRAM,
            OcrBlockType.FIGURE: RegionType.FIGURE,
            OcrBlockType.FORMULA: RegionType.TEXT,
        }
        typed: List[LayoutRegion] = []
        rest: List[OcrBlock] = []
        for block in blocks:
            region_type = direct.get(block.type)
            if region_type is not None:
                typed.append(
                    LayoutRegion(
                        region_type=region_type,
                        bbox=list(block.bbox),
                        confidence=block.confidence,
                        label=block.type.value,
                    )
                )
            else:
                rest.append(block)

        # 2. Table detection by row clustering among the rest.
        table_regions = self._find_tables(rest)
        table_blocks: List[OcrBlock] = []
        for region, consumed in table_regions:
            table_blocks.extend(consumed)
        in_table = set(id(b) for b in table_blocks)

        # 3. Question/answer segmentation over remaining text blocks.
        remaining = [b for b in rest if id(b) not in in_table]
        remaining.sort(key=lambda b: (b.bbox[1], b.bbox[0]))
        anchors = [
            (i, b) for i, b in enumerate(remaining)
            if _QUESTION_ANCHOR.match(b.text_clean)
        ]
        head = remaining[: anchors[0][0]] if anchors else remaining
        assigned = {id(b) for b in head}
        if head:
            bbox = self._union_bbox(head)
            typed.append(
                LayoutRegion(
                    region_type=RegionType.TEXT, bbox=bbox,
                    confidence=sum(b.confidence for b in head) / len(head),
                    label="leading text",
                )
            )

        for anchor_idx, (i, anchor) in enumerate(anchors):
            assigned.add(id(anchor))
            seg_end = anchors[anchor_idx + 1][1].bbox[1] if anchor_idx + 1 < len(anchors) else h
            segment = [
                b for b in remaining
                if b.bbox[1] >= anchor.bbox[3] and b.bbox[1] < seg_end
            ]
            assigned.update(id(b) for b in segment)
            # QUESTION region: the label block expanded to the segment span.
            q_bbox = [anchor.bbox[0], anchor.bbox[1], max(anchor.bbox[2], w // 2), seg_end]
            typed.append(
                LayoutRegion(
                    region_type=RegionType.QUESTION,
                    bbox=q_bbox,
                    confidence=anchor.confidence,
                    label=anchor.text_clean,
                )
            )
            if segment:
                ans_bbox = self._union_bbox(segment)
                typed.append(
                    LayoutRegion(
                        region_type=RegionType.ANSWER,
                        bbox=ans_bbox,
                        confidence=sum(b.confidence for b in segment) / len(segment),
                        label=f"answer for {anchor.text_clean}",
                    )
                )

        tail = [b for b in remaining if id(b) not in assigned]
        if tail:
            bbox = self._union_bbox(tail)
            typed.append(
                LayoutRegion(
                    region_type=RegionType.TEXT, bbox=bbox,
                    confidence=sum(b.confidence for b in tail) / len(tail),
                    label="trailing text",
                )
            )

        typed.extend(region for region, _ in table_regions)
        return self._order_top_down(typed)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _union_bbox(blocks: List[OcrBlock]) -> List[int]:
        x1 = min(b.bbox[0] for b in blocks)
        y1 = min(b.bbox[1] for b in blocks)
        x2 = max(b.bbox[2] for b in blocks)
        y2 = max(b.bbox[3] for b in blocks)
        return [x1, y1, x2, y2]

    def _find_tables(self, blocks: List[OcrBlock]) -> List:
        """Detect cell rows (≥2 side-by-side clusters) and merge into regions."""
        if not blocks:
            return []
        by_row: dict = {}
        for b in blocks:
            cy = (b.bbox[1] + b.bbox[3]) // 2
            key = None
            for existing in by_row:
                if abs(existing - cy) <= _TABLE_ROW_TOLERANCE:
                    key = existing
                    break
            by_row.setdefault(key if key is not None else cy, []).append(b)

        cell_rows = [row for row in by_row.values() if len(row) >= 2]
        if len(cell_rows) < 2:
            return []
        consumed: List[OcrBlock] = [b for row in cell_rows for b in row]
        x1 = min(b.bbox[0] for b in consumed)
        y1 = min(b.bbox[1] for b in consumed)
        x2 = max(b.bbox[2] for b in consumed)
        y2 = max(b.bbox[3] for b in consumed)
        conf = sum(b.confidence for b in consumed) / len(consumed)
        region = LayoutRegion(
            region_type=RegionType.TABLE,
            bbox=[x1, y1, x2, y2],
            confidence=conf,
            label=f"table ({len(cell_rows)} rows)",
        )
        return [(region, consumed)]

    @staticmethod
    def _order_top_down(regions: List[LayoutRegion]) -> List[LayoutRegion]:
        return sorted(regions, key=lambda r: (r.bbox[1], r.bbox[0]))


def default_layout_adapter() -> LayoutAdapter:
    """The runnable local analyzer (declared model still available explicitly)."""
    return RuleLayoutAdapter()