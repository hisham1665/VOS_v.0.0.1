"""Phase 4 OCR and document understanding (implementation plan Phase 4).

Exposes the OCR service surface:

  * structured output schema (`models`),
  * confidence extraction (`confidence`),
   * OCR model adapters -- configured Ollama/Hugging Face adapters, declared
     ML slots, and the runnable local structural engine (`adapters`),
  * layout analysis (`layout`),
  * the orchestrating OCR service (`pipeline.run_ocr` / `run_document`).

Remote model transports are lazy and optional; when they are unavailable the
runnable local structural adapter keeps the whole Phase-4 ladder executable and
the confidence gate routes the page to human review.
"""

from aos_v0.exam.ocr.adapters import (
    DeclaredModelAdapter,
    HuggingFaceOcrAdapter,
    LocalStructuralAdapter,
    OcrAdapter,
    OllamaOcrAdapter,
    PaddleOcrAdapter,
    QwenVlOcrAdapter,
    TrocrHandwrittenAdapter,
    TrocrLargeHandwrittenAdapter,
    TrocrPrintedAdapter,
    default_adapters,
)
from aos_v0.exam.ocr.confidence import (
    confidence_report,
    document_confidence,
    mean_confidence,
    page_confidence,
)
from aos_v0.exam.ocr.layout import (
    DeclaredLayoutAdapter,
    LayoutAdapter,
    RuleLayoutAdapter,
    default_layout_adapter,
)
from aos_v0.exam.ocr.models import (
    LayoutRegion,
    OcrAdapterAttempt,
    OcrAdapterAttemptStatus,
    OcrBlock,
    OcrBlockType,
    OcrDocument,
    OcrError,
    OcrLine,
    OcrPage,
    OcrRecognitionError,
    OcrResult,
    OcrSettings,
    OcrStatus,
    OcrStrategy,
    OcrUnavailableError,
    RegionType,
)
from aos_v0.exam.ocr.pipeline import main as ocr_cli_main
from aos_v0.exam.ocr.pipeline import run_document, run_ocr

__all__ = [
    "DeclaredLayoutAdapter",
    "DeclaredModelAdapter",
    "HuggingFaceOcrAdapter",
    "LayoutAdapter",
    "LayoutRegion",
    "LocalStructuralAdapter",
    "OcrAdapter",
    "OcrAdapterAttempt",
    "OcrAdapterAttemptStatus",
    "OcrBlock",
    "OcrBlockType",
    "OcrDocument",
    "OcrError",
    "OcrLine",
    "OcrPage",
    "OcrRecognitionError",
    "OcrResult",
    "OcrSettings",
    "OcrStatus",
    "OcrStrategy",
    "OcrUnavailableError",
    "OllamaOcrAdapter",
    "PaddleOcrAdapter",
    "QwenVlOcrAdapter",
    "RegionType",
    "RuleLayoutAdapter",
    "TrocrHandwrittenAdapter",
    "TrocrLargeHandwrittenAdapter",
    "TrocrPrintedAdapter",
    "confidence_report",
    "default_adapters",
    "default_layout_adapter",
    "document_confidence",
    "mean_confidence",
    "ocr_cli_main",
    "page_confidence",
    "run_document",
    "run_ocr",
]
