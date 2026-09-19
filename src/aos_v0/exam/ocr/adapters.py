"""OCR model adapters (plan Phase 4 deliverable).

Two families live here:

  * **Declared adapters** for the Phase-1 selected models (PP-OCRv6, TrOCR
    printed/handwritten, Qwen2.5-VL). They carry the model's identity from
    `exam/model_selection.py` and the capabilities it serves, but their
    `run()` raises :class:`OcrUnavailableError`: no execution transport exists
    yet (integration-spec gap G3 -- PaddleOCR/Transformers/vLLM wiring is
    Phase 8). This mirrors the declared-only exam resources.

  * **LocalStructuralAdapter** -- a runnable, pure-Pillow engine with no ML
    dependencies. It detects text-line bands and bounding boxes on a clean
    page, but it *cannot transcribe characters*, so every block is emitted
    untranscribed (`text=""`, `transcribed=False`) with a structurally-derived
    confidence capped well below the review gate. Its role in the fallback
    chain is honest degradation: in an environment with no model transport it
    produces real geometry, never fabricated text, and the confidence gate
    correctly escalates to human review.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from PIL import Image, ImageStat

from aos_v0.exam.model_selection import selection_for_capability
from aos_v0.exam.ocr.models import (
    OcrBlock,
    OcrBlockType,
    OcrRecognitionError,
    OcrUnavailableError,
)

#: Structural confidence ceiling: the local engine can place ink correctly but
#: cannot read it, so it must never report a transcription-confidence above the
#: low gate. Stays below OcrSettings.confidence_low (0.6).
_LOCAL_STRUCTURAL_CEILING = 0.55

_CRC_CEILING = 0.45  # heavy blur further drops the ceiling


# ---------------------------------------------------------------------------
# Adapter interface
# ---------------------------------------------------------------------------


class OcrAdapter(ABC):
    """Uniform adapter surface the service runs in its fallback chain."""

    adapter_id: str = "base"
    model_name: str = ""
    model_repo: str = ""
    capabilities: List[str] = []
    description: str = ""

    @abstractmethod
    def run(self, image: Image.Image, hints: Optional[dict] = None) -> List[OcrBlock]:
        """OCR one normalized page image into blocks (may raise OcrError)."""


# ---------------------------------------------------------------------------
# Declared adapters (model transports not wired yet -- gap G3)
# ---------------------------------------------------------------------------


class DeclaredModelAdapter(OcrAdapter):
    """A selected model whose transport is declared-only.

    Construction pulls the model's identity from the Phase-1 selection so the
    adapter and the registry always agree on the model; running it raises
    :class:`OcrUnavailableError` until a real transport is wired.
    """

    capability_name: str = ""
    _known_capabilities: List[str] = []

    def __post_init__(self):
        pass

    def __init__(
        self,
        adapter_id: str,
        capability_name: str,
        capabilities: List[str],
        model_name: str = "",
        model_repo: str = "",
        reason: str = "",
    ):
        selection = selection_for_capability(capability_name)
        primary = selection.primary if selection is not None else None
        self.adapter_id = adapter_id
        self.capability_name = capability_name
        self.capabilities = capabilities
        self.model_name = model_name or (primary.name if primary else "unassigned")
        self.model_repo = model_repo or (primary.hf_repo if primary else "unassigned")
        self.description = (
            f"declared adapter for '{self.model_name}' "
            f"({self.model_repo}); serves {', '.join(capabilities)}"
        )
        self._unavailable_reason = reason or (
            f"execution transport for '{self.model_name}' is not wired "
            f"(integration-spec gap G3; Phase 8)"
        )

    def run(self, image: Image.Image, hints: Optional[dict] = None) -> List[OcrBlock]:
        raise OcrUnavailableError(self._unavailable_reason)


class PaddleOcrAdapter(DeclaredModelAdapter):
    """PP-OCRv6 (primary DOCUMENT_OCR) -- declared adapter."""

    def __init__(self):
        super().__init__(
            adapter_id="paddle_ocr_v6",
            capability_name="DOCUMENT_OCR",
            capabilities=["document.ocr", "document.layout"],
            reason=(
                "PP-OCRv6 requires a PaddleOCR transport (paddleocr/paddlepaddle), "
                "which is not installed here and not wired (gap G3)"
            ),
        )


class TrocrPrintedAdapter(DeclaredModelAdapter):
    """TrOCR base printed (DOCUMENT_OCR fallback) -- declared adapter."""

    def __init__(self):
        super().__init__(
            adapter_id="trocr_base_printed",
            capability_name="DOCUMENT_OCR",
            capabilities=["document.ocr"],
            model_repo="microsoft/trocr-base-printed",
            reason=(
                "TrOCR requires a transformers/torch transport, which is not "
                "installed here and not wired (gap G3)"
            ),
        )


class TrocrHandwrittenAdapter(DeclaredModelAdapter):
    """TrOCR base handwritten (primary HANDWRITING_OCR) -- declared adapter."""

    def __init__(self):
        super().__init__(
            adapter_id="trocr_base_handwritten",
            capability_name="HANDWRITING_OCR",
            capabilities=["handwriting.ocr"],
            model_repo="microsoft/trocr-base-handwritten",
            reason=(
                "TrOCR requires a transformers/torch transport, which is not "
                "installed here and not wired (gap G3)"
            ),
        )


class TrocrLargeHandwrittenAdapter(DeclaredModelAdapter):
    """TrOCR large handwritten (HANDWRITING_OCR fallback) -- declared adapter."""

    def __init__(self):
        super().__init__(
            adapter_id="trocr_large_handwritten",
            capability_name="HANDWRITING_OCR",
            capabilities=["handwriting.ocr"],
            model_repo="microsoft/trocr-large-handwritten",
            reason=(
                "TrOCR requires a transformers/torch transport, which is not "
                "installed here and not wired (gap G3)"
            ),
        )


class QwenVlOcrAdapter(DeclaredModelAdapter):
    """Qwen2.5-VL vision fallback (HANDWRITING_OCR stride 2) -- declared adapter."""

    def __init__(self):
        super().__init__(
            adapter_id="qwen_vl_vision_fallback",
            capability_name="HANDWRITING_OCR",
            capabilities=["handwriting.ocr", "vision.understanding"],
            model_repo="Qwen/Qwen2.5-VL-7B-Instruct",
            reason=(
                "Qwen2.5-VL requires a VLM transport (transformers/vLLM/ollama), "
                "which is not wired for the exam volume (gap G3)"
            ),
        )


# ---------------------------------------------------------------------------
# Runnable local structural engine (no ML)
# ---------------------------------------------------------------------------


class LocalStructuralAdapter(OcrAdapter):
    """Pure-Pillow heuristic engine: locates text bands, cannot transcribe.

    Detection:
      * rows whose ink fraction exceeds a threshold are joined into bands
        (tolerating tiny gaps) -- on a clean answer sheet these are the header
        band and the answer lines;
      * each band's horizontal ink extent becomes the block bbox;
      * the top, wide, tall band is classified HEADER (candidate student
        info), everything else TEXT.

    Transcription is intentionally impossible here, so blocks are emitted with
    ``text=""``, ``transcribed=False`` and a confidence capped at
    ``_LOCAL_STRUCTURAL_CEILING`` -- under the review gate. A page routed here
    therefore lands in human review, which is the correct Phase-4 outcome for
    OCR that cannot actually read.
    """

    adapter_id = "local_structural"
    model_name = "heuristic row-band detector (no ML)"
    model_repo = "local"
    capabilities = ["document.ocr", "document.layout"]
    description = (
        "Pure-Pillow structural OCR: detects text bands and bboxes on clean "
        "forms but cannot transcribe; confidence is capped low so the gate "
        "escalates to human review instead of fabricating text."
    )

    #: ink threshold (luma below this = ink) for row/column projection.
    INK_LUMA = 128
    #: fraction of dark pixels in a row for it to count as text.
    ROW_INK_FRACTION = 0.01
    #: fraction of ink pixels in a column within a band to count as covered.
    COL_INK_FRACTION = 0.005
    #: row-gap tolerated when merging text rows into bands (px).
    BAND_GAP = 3

    def _project(self, image: Image.Image) -> tuple:
        """Per-row ink counts of a 'L' image ('luma<INK_LUMA' = ink)."""
        luma = image.convert("L")
        w, h = luma.size
        pixels = bytes(luma.tobytes())
        row_ink = [0] * h
        for y in range(h):
            base = y * w
            row_ink[y] = sum(1 for p in pixels[base : base + w] if p < self.INK_LUMA)
        return row_ink, w, h

    def run(self, image: Image.Image, hints: Optional[dict] = None) -> List[OcrBlock]:
        if hints is None:
            hints = {}
        if hints.get("blank") or hints.get("dark") or hints.get("washed"):
            # Degenerate lighting: the projection carries no usable signal.
            return []

        img = image.convert("L")
        try:
            stat = ImageStat.Stat(img)
        except Exception as exc:  # noqa: BLE001 - malformed image surface
            raise OcrRecognitionError(f"cannot analyse page image: {exc}") from exc
        mean_luma = float(stat.mean[0]) if stat.mean else 255.0
        if mean_luma < 70 or mean_luma > 248:
            return []

        row_ink, w, h = self._project(img)
        text_rows = [
            y for y in range(h) if row_ink[y] / max(1, w) >= self.ROW_INK_FRACTION
        ]
        bands = self._bands_from_rows(text_rows)

        blocks: List[OcrBlock] = []
        for y0, y1 in bands:
            band = img.crop((0, y0, w, y1))
            bw, bh = band.size
            band_px = bytes(band.tobytes())
            col_ink = [0] * bw
            for y in range(bh):
                base = y * bw
                for x, p in enumerate(band_px[base : base + bw]):
                    if p < self.INK_LUMA:
                        col_ink[x] += 1
            xs = [
                x
                for x, ink in enumerate(col_ink)
                if ink / max(1, bh) >= self.COL_INK_FRACTION
            ]
            if not xs:
                continue
            x_min, x_max = min(xs), max(xs)
            band_h = y1 - y0
            is_header = (
                y0 <= h * 0.25
                and (x_max - x_min) >= w * 0.6
                and band_h >= 60
            )
            confidence = self._block_confidence(
                img, x_min, x_max, y0, y1, hints
            )
            blocks.append(
                OcrBlock(
                    type=OcrBlockType.HEADER if is_header else OcrBlockType.TEXT,
                    text="",
                    bbox=[x_min, y0, x_max, y1],
                    confidence=confidence,
                    transcribed=False,
                )
            )
        return blocks

    @staticmethod
    def _bands_from_rows(text_rows: List[int]) -> List[tuple]:
        """Join text rows into (y0, y1] bands, tolerating small gaps."""
        if not text_rows:
            return []
        bands: List[tuple] = []
        start = prev = text_rows[0]
        for y in text_rows[1:]:
            if y - prev > 1 + LocalStructuralAdapter.BAND_GAP:
                bands.append((start, prev + 1))
                start = y
            prev = y
        bands.append((start, prev + 1))
        return bands

    def _block_confidence(
        self,
        img: Image.Image,
        x_min: int,
        x_max: int,
        y0: int,
        y1: int,
        hints: dict,
    ) -> float:
        crop = img.crop((x_min, y0, x_max, y1))
        w, h = crop.size
        area = max(1, w * h)
        pixels = bytes(crop.tobytes())
        dark = sum(1 for p in pixels if p < 64)
        light = sum(1 for p in pixels if p < self.INK_LUMA)
        coverage = light / area
        crisp = dark / area
        structural = 0.25 + 0.2 * min(1.0, coverage * 8.0) + 0.1 * min(1.0, crisp * 4.0)
        ceiling = _CRC_CEILING if hints.get("blurry") else _LOCAL_STRUCTURAL_CEILING
        return round(min(ceiling, max(0.05, structural)), 4)


# ---------------------------------------------------------------------------
# Default chains
# ---------------------------------------------------------------------------


def default_adapters(strategy: str = "auto", *, local: bool = True) -> List[OcrAdapter]:
    """The fallback chain for a strategy (plan Phase 4 ladder)."""
    printed = [PaddleOcrAdapter(), TrocrPrintedAdapter()]
    handwritten = [TrocrHandwrittenAdapter(), TrocrLargeHandwrittenAdapter()]
    vision = [QwenVlOcrAdapter()]
    chain: List[OcrAdapter]
    if strategy == "handwritten":
        chain = handwritten + vision
    elif strategy == "printed":
        chain = printed + vision
    else:
        chain = printed + handwritten + vision
    if local:
        chain = chain + [LocalStructuralAdapter()]
    return chain