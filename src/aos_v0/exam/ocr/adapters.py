"""OCR model adapters (plan Phase 4 deliverable).

Two families live here:

  * **Remote vision adapters** for configured Hugging Face and Ollama
    multimodal models. They send a normalized page image with a strict JSON
    transcription prompt and convert the response into the domain OCR schema.
    They are lazy: no client or network connection is created at import time.

  * **Declared adapters** for the Phase-1 selected models (PP-OCRv6, TrOCR
    printed/handwritten, Qwen2.5-VL). They remain available as honest metadata
    and future line-OCR slots, but their `run()` raises
    :class:`OcrUnavailableError` until their specialised transports are added.

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
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, List, Mapping, Optional

from PIL import Image, ImageStat

from aos_v0.exam.model_selection import selection_for_capability
from aos_v0.exam.ocr.models import (
    OcrBlock,
    OcrBlockType,
    OcrRecognitionError,
    OcrUnavailableError,
)
from aos_v0.providers.errors import ProviderError

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
# Shared remote-VLM contract
# ---------------------------------------------------------------------------


OCR_JSON_PROMPT = """You are transcribing one scanned student answer-booklet page.
Return JSON only, with this exact top-level shape:
{"blocks":[{"type":"text|header|question_label|answer|table|diagram|figure|formula",
"text":"...","bbox":[x1,y1,x2,y2],"confidence":0.0,"transcribed":true}],
"page_confidence":0.0}

The image pixel dimensions are supplied separately. Use pixel coordinates with a
top-left origin. Preserve the student's written words, spelling, punctuation,
engineering symbols, units, subscripts, superscripts, and mathematical steps.
Represent a formula as type "formula" and use readable LaTeX when possible.
Represent a table or diagram as its own block and describe only visible labels,
connections, values, or shapes. Do not solve, correct, summarize, or infer
missing content. If a region cannot be read, emit an empty text block with
transcribed=false and low confidence instead of guessing. Keep blocks in
top-to-bottom, left-to-right reading order. Confidence is a conservative
readability estimate in [0,1], not a correctness score.
"""


_BLOCK_TYPE_ALIASES = {
    "heading": OcrBlockType.HEADER,
    "title": OcrBlockType.HEADER,
    "question": OcrBlockType.QUESTION_LABEL,
    "question_number": OcrBlockType.QUESTION_LABEL,
    "question-label": OcrBlockType.QUESTION_LABEL,
    "response": OcrBlockType.ANSWER,
    "equation": OcrBlockType.FORMULA,
    "math": OcrBlockType.FORMULA,
    "image": OcrBlockType.FIGURE,
}


def _image_data_url(image: Image.Image) -> str:
    """Encode a page as a portable PNG data URL for HF chat completion."""
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bbox(value: Any, size: tuple[int, int], fallback: List[int]) -> List[int]:
    """Normalize pixel or [0,1] model coordinates into a valid page bbox."""
    width, height = size
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return fallback
    numbers = [_number(item, -1) for item in value]
    if any(item < 0 for item in numbers):
        return fallback
    if max(numbers) <= 1.0 and width > 1 and height > 1:
        numbers = [
            numbers[0] * width,
            numbers[1] * height,
            numbers[2] * width,
            numbers[3] * height,
        ]
    x1, y1, x2, y2 = [int(round(item)) for item in numbers]
    x1 = max(0, min(width, x1))
    y1 = max(0, min(height, y1))
    x2 = max(0, min(width, x2))
    y2 = max(0, min(height, y2))
    if x2 <= x1 or y2 <= y1:
        return fallback
    return [x1, y1, x2, y2]


def _block_type(value: Any) -> OcrBlockType:
    normalized = str(value or "text").strip().lower().replace(" ", "_")
    return _BLOCK_TYPE_ALIASES.get(normalized, OcrBlockType(normalized)
                                  if normalized in {item.value for item in OcrBlockType}
                                  else OcrBlockType.TEXT)


def _json_payload(text: str) -> Any:
    """Parse JSON from a model response, including fenced/leading prose."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]).strip() if len(lines) > 2 else ""
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for marker in ("{", "["):
            start = cleaned.find(marker)
            if start < 0:
                continue
            try:
                value, _ = decoder.raw_decode(cleaned[start:])
                return value
            except json.JSONDecodeError:
                continue
    return None


def parse_ocr_response(response: Any, size: tuple[int, int]) -> List[OcrBlock]:
    """Turn a VLM response into safe structured blocks.

    A plain-text response is retained as a full-page, low-confidence block so
    operators can review it, but it cannot pass the normal 0.6 acceptance gate
    without a model-provided structured confidence.
    """
    raw_text = response if isinstance(response, str) else json.dumps(response)
    payload = _json_payload(raw_text)
    page_confidence: Optional[float] = None
    items: Any = None
    if isinstance(payload, Mapping):
        items = payload.get("blocks") or payload.get("regions")
        page_confidence = _number(
            payload.get("page_confidence", payload.get("confidence")), -1
        )
        if items is None and payload.get("text") is not None:
            items = [payload]
    elif isinstance(payload, list):
        items = payload

    if not isinstance(items, list):
        text = raw_text.strip()
        if not text:
            raise OcrRecognitionError("OCR model returned an empty response")
        return [
            OcrBlock(
                type=OcrBlockType.TEXT,
                text=text,
                bbox=[0, 0, size[0], size[1]],
                confidence=0.55,
                transcribed=True,
            )
        ]

    blocks: List[OcrBlock] = []
    count = max(1, len(items))
    width, height = size
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            continue
        text_value = item.get("text", item.get("content", ""))
        text = text_value if isinstance(text_value, str) else str(text_value or "")
        fallback = [0, (index * height) // count, width,
                    ((index + 1) * height) // count]
        bbox = _bbox(item.get("bbox", item.get("box")), size, fallback)
        transcribed = bool(item.get("transcribed", bool(text.strip())))
        confidence_default = page_confidence if page_confidence >= 0 else 0.55
        confidence = max(
            0.0,
            min(1.0, _number(item.get("confidence", item.get("score")),
                              confidence_default)),
        )
        if not text.strip() or not transcribed:
            transcribed = False
            confidence = min(confidence, 0.25)
        blocks.append(
            OcrBlock(
                type=_block_type(item.get("type")),
                text=text,
                bbox=bbox,
                confidence=round(confidence, 4),
                transcribed=transcribed,
            )
        )
    if not blocks:
        raise OcrRecognitionError("OCR model returned no readable blocks")
    return blocks


class _RemoteOcrAdapter(OcrAdapter):
    """Common prompt/response handling for remote multimodal OCR models."""

    capabilities = ["document.ocr", "document.layout", "handwriting.ocr",
                    "vision.understanding"]

    def __init__(self, *, generator: Optional[Callable[[Image.Image, str], Any]] = None):
        self._generator = generator

    def _generate(self, image: Image.Image, prompt: str) -> Any:
        raise NotImplementedError

    def run(self, image: Image.Image, hints: Optional[dict] = None) -> List[OcrBlock]:
        if hints and hints.get("blank"):
            return []
        prompt = (
            f"{OCR_JSON_PROMPT}\nPage pixel dimensions: {image.width} x {image.height}."
        )
        if hints:
            issues = ", ".join(sorted(key for key, value in hints.items() if value))
            if issues:
                prompt += f"\nKnown scan issues: {issues}. Be extra conservative."
        try:
            response = self._generate(image, prompt)
        except OcrError:
            raise
        except ProviderError as exc:
            raise OcrUnavailableError(f"{self.adapter_id} provider unavailable: {type(exc).__name__}") from exc
        except (ConnectionError, TimeoutError, OSError, ValueError) as exc:
            raise OcrUnavailableError(
                f"{self.adapter_id} transport unavailable: {type(exc).__name__}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - adapter boundary is typed below
            raise OcrUnavailableError(
                f"{self.adapter_id} transport failed: {type(exc).__name__}"
            ) from exc
        return parse_ocr_response(response, (image.width, image.height))


class OllamaOcrAdapter(_RemoteOcrAdapter):
    """OCR through an Ollama OpenAI-compatible multimodal endpoint.

    The default is ``deepseek-ocr:3b`` because it is OCR-focused. Operators can
    choose another installed vision model in ``aos.yaml`` under ``models.ocr``
    or with ``AOS_OLLAMA_OCR_MODEL``. The gateway is constructed only on first
    use, preserving offline imports and tests.
    """

    adapter_id = "ollama_ocr"
    model_name = "deepseek-ocr:3b"
    model_repo = "ollama/deepseek-ocr"
    description = "Configured Ollama OCR/VLM adapter with structured JSON output."

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        profile: str = "ocr",
        gateway: Any = None,
        generator: Optional[Callable[[Image.Image, str], Any]] = None,
    ):
        super().__init__(generator=generator)
        self.model_name = model or os.environ.get("AOS_OLLAMA_OCR_MODEL", self.model_name)
        self.profile = profile
        self._gateway = gateway

    def _generate(self, image: Image.Image, prompt: str) -> Any:
        if self._generator is not None:
            return self._generator(image, prompt)
        if self._gateway is None:
            from aos_v0.providers.gateway import ModelGateway
            from aos_v0.settings import load_settings

            settings = load_settings()
            if self.profile not in settings.models:
                raise OcrUnavailableError(
                    f"AOS model profile '{self.profile}' is not configured"
                )
            # Environment/model overrides are applied to a fresh settings object,
            # never to the process-global YAML or another request's gateway.
            settings.models[self.profile].model = self.model_name
            self._gateway = ModelGateway(settings)
        temporary = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        path = Path(temporary.name)
        try:
            temporary.close()
            image.convert("RGB").save(path, format="PNG")
            return self._gateway.image(str(path), instruction=prompt, role=self.profile)
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


class HuggingFaceOcrAdapter(_RemoteOcrAdapter):
    """OCR through Hugging Face Inference Providers chat vision models."""

    adapter_id = "huggingface_ocr"
    model_name = "Qwen/Qwen2.5-VL-7B-Instruct"
    model_repo = model_name
    description = "Configured Hugging Face multimodal OCR/VLM adapter."

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        token: Optional[str] = None,
        client: Any = None,
        generator: Optional[Callable[[Image.Image, str], Any]] = None,
    ):
        super().__init__(generator=generator)
        self.model_name = model or os.environ.get(
            "AOS_HF_OCR_MODEL", self.model_name
        )
        self.model_repo = self.model_name
        self._provider_name = provider
        self._token = token
        self._client = client
        self._provider = None

    def _generate(self, image: Image.Image, prompt: str) -> Any:
        if self._generator is not None:
            return self._generator(image, prompt)
        if self._provider is None:
            from aos_v0.providers.hf import HFProvider

            self._provider = HFProvider(
                token=self._token or os.environ.get("HF_TOKEN") or os.environ.get("HUG"),
                model=self.model_name,
                provider=self._provider_name,
                client=self._client,
            )
        result = self._provider.execute(
            model=self.model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": _image_data_url(image)}},
                    ],
                }
            ],
            temperature=0.0,
            max_tokens=4096,
        )
        return result.text


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


def default_adapters(
    strategy: str = "auto",
    *,
    local: bool = True,
    remote: bool = False,
) -> List[OcrAdapter]:
    """Build the fallback chain for a strategy.

    ``remote`` is explicit so library callers and tests remain deterministic and
    offline by default. The CLI and the full exam orchestrator opt in because
    they are the real model-backed application paths. Every remote adapter is
    still lazy and an unavailable provider simply leaves the page for the next
    adapter or human review.
    """
    def _remote() -> List[OcrAdapter]:
        return [OllamaOcrAdapter(), HuggingFaceOcrAdapter()] if remote else []

    printed = _remote() + [PaddleOcrAdapter(), TrocrPrintedAdapter()]
    handwritten = _remote() + [TrocrHandwrittenAdapter(), TrocrLargeHandwrittenAdapter()]
    vision = [QwenVlOcrAdapter()]
    chain: List[OcrAdapter]
    if strategy == "handwritten":
        chain = handwritten + vision
    elif strategy == "printed":
        chain = printed + vision
    else:
        chain = _remote() + [
            PaddleOcrAdapter(),
            TrocrPrintedAdapter(),
            TrocrHandwrittenAdapter(),
            TrocrLargeHandwrittenAdapter(),
        ] + vision
    if local:
        chain = chain + [LocalStructuralAdapter()]
    return chain
