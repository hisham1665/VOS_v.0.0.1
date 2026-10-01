"""Central configuration for Medical AOS.

Every tunable lives here and is overridable through environment variables so
that the same code runs on a laptop GPU, a CPU-only box, or fully remote via
the Hugging Face Inference API.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "").strip()
HF_TOKEN: str = (
    os.environ.get("HF_TOKEN")
    or os.environ.get("HUGGINGFACEHUB_API_TOKEN")
    or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    or ""
).strip()


# --------------------------------------------------------------------------
# Hugging Face model identifiers
#
# Each entry is a Hub repo id. Override any of them with the matching env var
# to swap in a different checkpoint without touching code.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ModelConfig:
    """Hugging Face checkpoints used by each capability."""

    # Multimodal medical VLM that writes the radiological impression.
    # Large (4B+), so it runs remotely by default on small GPUs.
    report_vlm: str = os.environ.get("MAOS_REPORT_VLM", "google/medgemma-4b-it")

    # Fallback VLM when the primary is gated/unavailable for this token.
    report_vlm_fallback: str = os.environ.get(
        "MAOS_REPORT_VLM_FALLBACK", "Qwen/Qwen2.5-VL-7B-Instruct"
    )

    # Supervised screeners/specialists live in capabilities/model_zoo.py, one
    # entry per modality+region. MAOS_CXR_CLASSIFIER still overrides the chest
    # X-ray screener there; MAOS_MODEL_ZOO adds or replaces entries.

    # Zero-shot CLIP used for modality / body-region routing. PubMedCLIP is
    # CLIP fine-tuned on radiology figure captions (ROCO); on a 230-image
    # benchmark it routed 96% correctly vs 60% for general CLIP, which called
    # every brain MRI a CT. ~600MB, runs local.
    modality_clip: str = os.environ.get(
        "MAOS_MODALITY_CLIP", "flaviagiammarino/pubmed-clip-vit-base-patch32"
    )

    # General CLIP used only as the "is this a medical image at all?" gate.
    # PubMedCLIP has seen almost no everyday photos and is bad at this; general
    # CLIP is good at it. ~600MB, runs local.
    nonmedical_clip: str = os.environ.get(
        "MAOS_NONMEDICAL_CLIP", "openai/clip-vit-base-patch32"
    )

    # Text summarisation fallback when no hosted LLM is configured.
    summarizer: str = os.environ.get("MAOS_SUMMARIZER", "facebook/bart-large-cnn")

    # Hosted text LLM (Groq) used for planning, summarisation and synthesis.
    text_llm: str = os.environ.get("MAOS_TEXT_LLM", "openai/gpt-oss-120b")


MODELS = ModelConfig()


# --------------------------------------------------------------------------
# Runtime / performance
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class RuntimeConfig:
    """Execution knobs: where models run and how hard we push them."""

    # "auto" -> local for small models if torch+transformers present, remote
    # for large ones. Force with "local" or "remote".
    backend: str = os.environ.get("MAOS_BACKEND", "auto").lower()

    # torch device for local inference: "auto" | "cuda" | "cpu".
    device: str = os.environ.get("MAOS_DEVICE", "auto").lower()

    # Half precision on CUDA roughly halves VRAM and speeds up inference.
    fp16: bool = _env_bool("MAOS_FP16", True)

    # Global ceiling on concurrent capability calls. Prevents a wide DAG from
    # spawning one thread per node and thrashing a small GPU.
    max_workers: int = _env_int("MAOS_MAX_WORKERS", 8)

    # Concurrency limit for *local* GPU work specifically. A 4GB card should
    # not run two vision models at once.
    max_gpu_workers: int = _env_int("MAOS_MAX_GPU_WORKERS", 1)

    # Per-capability wall clock budget, seconds.
    call_timeout: float = _env_float("MAOS_CALL_TIMEOUT", 180.0)

    # Retry policy for transient network/model errors.
    max_retries: int = _env_int("MAOS_MAX_RETRIES", 3)
    retry_base_delay: float = _env_float("MAOS_RETRY_BASE_DELAY", 1.0)

    # Batch size for classifier passes over a study folder.
    batch_size: int = _env_int("MAOS_BATCH_SIZE", 8)

    # Longest edge (px) an image is resized to before inference or encoding.
    # Medical source files are often 3000px+; models see 512-896px anyway.
    max_image_edge: int = _env_int("MAOS_MAX_IMAGE_EDGE", 896)

    # JPEG quality used when re-encoding preprocessed images.
    jpeg_quality: int = _env_int("MAOS_JPEG_QUALITY", 90)

    # Cap on images analysed per study before sampling kicks in.
    max_images_per_study: int = max(0, _env_int("MAOS_MAX_IMAGES", 0))  # 0 = all

    # Explicit extraction budgets; exceeding one must produce a warning.
    max_file_mb: int = max(1, _env_int("MAOS_MAX_FILE_MB", 100))
    max_document_pages: int = max(1, _env_int("MAOS_MAX_DOCUMENT_PAGES", 200))
    max_document_chars: int = max(1000, _env_int("MAOS_MAX_DOCUMENT_CHARS", 2000000))
    document_chunk_chars: int = max(1000, _env_int("MAOS_DOCUMENT_CHUNK_CHARS", 8000))
    document_image_edge: int = max(512, _env_int("MAOS_DOCUMENT_IMAGE_EDGE", 1800))

    # Local pipelines kept in memory at once. The cascade can touch a dozen
    # checkpoints in one study; on a 4GB card they cannot all stay resident,
    # so the least recently used is evicted.
    max_resident_models: int = max(1, _env_int("MAOS_MAX_RESIDENT_MODELS", 4))

    # Disk cache for preprocessed images and model outputs.
    cache_enabled: bool = _env_bool("MAOS_CACHE", True)
    cache_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("MAOS_CACHE_DIR", str(ROOT / ".cache"))
        )
    )

    # Skip the extra LLM completeness audit round-trip in the planner.
    # The deterministic radiology planner does not need it.
    planner_completeness_check: bool = _env_bool("MAOS_COMPLETENESS_CHECK", False)

    # Emit a de-identification pass over extracted DICOM metadata.
    deidentify: bool = _env_bool("MAOS_DEIDENTIFY", True)

    log_level: str = os.environ.get("MAOS_LOG_LEVEL", "INFO").upper()


RUNTIME = RuntimeConfig()


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
OUTPUT_DIR = Path(os.environ.get("MAOS_OUTPUT_DIR", str(ROOT / "outputs")))
REPORT_DIR = OUTPUT_DIR / "reports"


SUPPORTED_IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
}
SUPPORTED_DICOM_SUFFIXES = {".dcm", ".dicom", ".ima", ""}
SUPPORTED_DOCUMENT_SUFFIXES = {
    ".pdf", ".txt", ".md", ".log", ".csv", ".tsv", ".json", ".ndjson",
    ".xml", ".html", ".htm", ".yaml", ".yml", ".rtf", ".docx", ".xlsx",
}


def describe() -> dict:
    """Return a redacted snapshot of the active configuration, for logs/docs."""
    return {
        "backend": RUNTIME.backend,
        "device": RUNTIME.device,
        "fp16": RUNTIME.fp16,
        "max_workers": RUNTIME.max_workers,
        "max_gpu_workers": RUNTIME.max_gpu_workers,
        "batch_size": RUNTIME.batch_size,
        "max_image_edge": RUNTIME.max_image_edge,
        "max_images_per_study": RUNTIME.max_images_per_study,
        "max_resident_models": RUNTIME.max_resident_models,
        "max_file_mb": RUNTIME.max_file_mb,
        "max_document_pages": RUNTIME.max_document_pages,
        "max_document_chars": RUNTIME.max_document_chars,
        "document_chunk_chars": RUNTIME.document_chunk_chars,
        "document_image_edge": RUNTIME.document_image_edge,
        "cache_enabled": RUNTIME.cache_enabled,
        "models": {
            "report_vlm": MODELS.report_vlm,
            "modality_clip": MODELS.modality_clip,
            "nonmedical_clip": MODELS.nonmedical_clip,
            "summarizer": MODELS.summarizer,
            "text_llm": MODELS.text_llm,
        },
        "groq_key_present": bool(GROQ_API_KEY),
        "hf_token_present": bool(HF_TOKEN),
    }
