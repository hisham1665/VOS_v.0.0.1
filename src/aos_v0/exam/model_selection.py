"""Phase 1 initial model registry -- capability-driven HF model selection.

This is the *survey output* the exam evaluator's dynamic selection will consume
(implementation plan, Phase 1). Every entry answers the same question, per
required capability: which Hugging Face models are the primary candidate, which
are fallbacks, what does running them cost in resources, and what constraints
gate their selection. The registry mirrors DOC1 5.1's separation of concerns:
the *declaration* (what a model is / what it needs) lives here as static data,
and measured runtime behaviour lives in the Capability Registry manifests and
the MODEL_BENCHMARK results.

Provenance discipline (mirrors providers/hf.py):

  * Model identities (name, HF repo, architecture, params) are either
    confirmed from Phase-1 research or explicitly marked `verify_repo=True`
    when the checkpoint's exact repo id could not be confirmed.
  * VRAM / RAM / latency figures are ESTIMATES to be validated by the
    benchmark; they are labelled as such and never asserted as measurements.
  * Licences are best-effort from the research pass; any licence flagged
    `non_commercial` must be resolved before production use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResourceProfile:
    """Rough footprint of one model, as selection constraints feed on."""
    params: str
    vram_gb: str            # estimate; benchmark must confirm
    ram_gb: str             # estimate; benchmark must confirm
    cpu_feasible: bool
    quantization: List[str]
    framework: str
    license: str
    expected_latency: str   # qualitative until benchmarked
    non_commercial: bool = False


@dataclass(frozen=True)
class ModelCandidate:
    """One candidate model for a capability (candidate record, plan Phase 1)."""
    name: str
    hf_repo: str
    architecture: str
    params: str
    modality: str
    input_format: str
    output_format: str
    context_length: str
    languages: str
    reasoning: str          # stated OCR / vision / reasoning strength
    profile: ResourceProfile
    limitations: List[str] = field(default_factory=list)
    fallback_candidates: List[str] = field(default_factory=list)
    verify_repo: bool = False


@dataclass(frozen=True)
class SelectionConstraints:
    """Hard/soft bounds model selection must satisfy for the capability."""
    min_quality: float
    max_latency_ms: int
    max_cost_usd: float
    min_context_tokens: int
    languages: List[str]
    notes: str = ""


@dataclass(frozen=True)
class CapabilitySelection:
    """One required capability -> primary + fallbacks + gates."""
    capability: str         # plan-ability key, e.g. "DOCUMENT_OCR"
    flag: str               # representative CAPABILITY_FLAGS member
    flags: List[str]        # full requirement set served by these models
    primary: ModelCandidate
    fallback: List[ModelCandidate]
    constraints: SelectionConstraints


# ---------------------------------------------------------------------------
# Capability aliasing: plan keys <- exam/core flags
# ---------------------------------------------------------------------------

# Plan-key -> capability flags the selected models must collectively provide.
PLAN_CAPABILITY_FLAGS: Dict[str, List[str]] = {
    "DOCUMENT_OCR": ["document.ocr", "document.layout"],
    "HANDWRITING_OCR": ["handwriting.ocr"],
    "DOCUMENT_LAYOUT": ["document.layout", "document.ocr"],
    "VISION_UNDERSTANDING": ["vision.understanding"],
    "SEMANTIC_EMBEDDING": ["semantic.embedding"],
    "SEMANTIC_EVALUATION": ["semantic.answer_evaluation", "rubric.evaluation",
                            "concept.extraction"],
    "GENERAL_REASONING": ["reasoning.deep", "reasoning.shallow"],
    "MATHEMATICAL_REASONING": ["mathematical.evaluation"],
    "DIAGRAM_UNDERSTANDING": ["diagram.evaluation", "vision.understanding"],
    "MULTILINGUAL_UNDERSTANDING": ["text.normalization", "semantic.embedding"],
    "STUDENT_ID_EXTRACTION": ["student_id.extraction"],
    "QUESTION_SEGMENTATION": ["question.segmentation"],
    "EVALUATION_RECONCILIATION": ["evaluation.reconciliation"],
}

# Selected candidates, keyed by plan capability name. See MODEL_SELECTION.md
# for the research narrative behind each choice.
EXAM_MODEL_SELECTION: Dict[str, CapabilitySelection] = {
    "DOCUMENT_OCR": CapabilitySelection(
        capability="DOCUMENT_OCR",
        flag="document.ocr",
        flags=["document.ocr", "document.layout"],
        constraints=SelectionConstraints(
            min_quality=0.90, max_latency_ms=60_000, max_cost_usd=0.05,
            min_context_tokens=512, languages=["en", "multilingual"],
            notes="OCR output must include per-block confidence for the "
                  "low-confidence fallback chain (plan Phase 4).",
        ),
        primary=ModelCandidate(
            name="PP-OCRv6 (PaddleOCR v3.7.0)",
            hf_repo="PaddlePaddle/PaddleOCR",
            architecture="PP-OCRv6 detection + recognition pipeline",
            params="~15-35M per stage (mobile/server variants)",
            modality="image -> text + bbox + confidence",
            input_format="page image (jpg/png/pdf)",
            output_format="text blocks, bounding boxes, confidence",
            context_length="page-level (chunked)",
            languages="100+ (PP-OCR multilingual)",
            reasoning="OCR; SOTA-level published accuracy incl. PaddleOCR-VL",
            profile=ResourceProfile(
                params="~15-35M", vram_gb="1-4 (int8/fp16)",
                ram_gb="4-8", cpu_feasible=True,
                quantization=["int8", "fp16"], framework="PaddlePaddle",
                license="Apache-2.0",
                expected_latency="100-500 ms/page on CPU; much faster on GPU",
            ),
            limitations=["Deployment is via `paddleocr` pip / Paddle deployment, "
                         "not a Transformers pipeline; HF entry is the repo, not a "
                         "serverless model id."],
            fallback_candidates=["microsoft/trocr-base-printed"],
        ),
        fallback=[
            ModelCandidate(
                name="TrOCR base printed",
                hf_repo="microsoft/trocr-base-printed",
                architecture="Vision-Transformer encoder + RoBERTa-style decoder",
                params="334M",
                modality="image -> text",
                input_format="cropped text-line image",
                output_format="plain text",
                context_length="~64-256 px per line crop",
                languages="en (single-line)",
                reasoning="Printed-text OCR (IAM/IIIT/wikipedia); strong on clean "
                          "printed lines, weak on layout and long pages",
                profile=ResourceProfile(
                    params="334M", vram_gb="<1 (fp16)", ram_gb="2-3",
                    cpu_feasible=True,
                    quantization=["fp16"], framework="PyTorch / Transformers",
                    license="MIT", expected_latency="50-200 ms/line on GPU",
                ),
                limitations=["Single-line crops only; needs a detection/layout step "
                             "first; English-centric."],
                fallback_candidates=["PaddlePaddle/PaddleOCR"],
            ),
        ],
    ),
    "HANDWRITING_OCR": CapabilitySelection(
        capability="HANDWRITING_OCR",
        flag="handwriting.ocr",
        flags=["handwriting.ocr"],
        constraints=SelectionConstraints(
            min_quality=0.80, max_latency_ms=90_000, max_cost_usd=0.05,
            min_context_tokens=512, languages=["en"],
            notes="Handwriting is the least reliable OCR stage; it feeds the "
                  "Phase-4 fallback chain (handwriting model -> VLM -> human), "
                  "never automatic zero marks.",
        ),
        primary=ModelCandidate(
            name="TrOCR base handwritten",
            hf_repo="microsoft/trocr-base-handwritten",
            architecture="Vision-Transformer encoder + RoBERTa-style decoder",
            params="334M",
            modality="image -> text",
            input_format="cropped line/handwriting image",
            output_format="plain text",
            context_length="~64-256 px per line crop",
            languages="en",
            reasoning="Handwritten line recognition (IAM dataset); the standard "
                      "open handwriting baseline",
            profile=ResourceProfile(
                params="334M", vram_gb="<1 (fp16)", ram_gb="2-3",
                cpu_feasible=True,
                quantization=["fp16"], framework="PyTorch / Transformers",
                license="MIT", expected_latency="100-400 ms/line on GPU",
            ),
            limitations=["Line-level crops; slower/less accurate on cursive or "
                         "marginal scans; needs layout to find handwriting regions."],
            fallback_candidates=["microsoft/trocr-large-handwritten",
                                 "Qwen/Qwen2.5-VL-7B-Instruct"],
        ),
        fallback=[
            ModelCandidate(
                name="TrOCR large handwritten",
                hf_repo="microsoft/trocr-large-handwritten",
                architecture="ViT-large encoder + RoBERTa decoder",
                params="~412M",
                modality="image -> text",
                input_format="cropped handwriting image",
                output_format="plain text",
                context_length="~64-256 px per line crop",
                languages="en",
                reasoning="Higher-capacity handwritten line model",
                profile=ResourceProfile(
                    params="412M", vram_gb="~1-2 (fp16)", ram_gb="3-4",
                    cpu_feasible=True,
                    quantization=["fp16"], framework="PyTorch / Transformers",
                    license="MIT", expected_latency="150-500 ms/line on GPU",
                ),
                limitations=["Line-level crops; English-centric."],
                fallback_candidates=["Qwen/Qwen2.5-VL-7B-Instruct"],
            ),
            ModelCandidate(
                name="Qwen2.5-VL-7B-Instruct (vision fallback)",
                hf_repo="Qwen/Qwen2.5-VL-7B-Instruct",
                architecture="Qwen2.5 LLM + ViT vision encoder (VLM)",
                params="7B",
                modality="image+text -> text",
                input_format="full page image",
                output_format="transcribed text / structured JSON",
                context_length="32,768",
                languages="multilingual",
                reasoning="Whole-page handwriting reading in context -- the "
                          "Phase-4 'vision fallback' step after dedicated OCR "
                          "fails",
                profile=ResourceProfile(
                    params="7B", vram_gb="~8 (int4) / ~16 (fp16)", ram_gb="12-20",
                    cpu_feasible=False,
                    quantization=["int4", "int8", "fp16"],
                    framework="PyTorch / Transformers",
                    license="Apache-2.0",
                    expected_latency="1-4 s/page on GPU",
                ),
                limitations=["Need page-level context; heavier VRAM; slower than "
                             "line OCR models."],
                fallback_candidates=["OpenGVLab/InternVL2_5-8B"],
            ),
        ],
    ),
    "DOCUMENT_LAYOUT": CapabilitySelection(
        capability="DOCUMENT_LAYOUT",
        flag="document.layout",
        flags=["document.layout", "document.ocr"],
        constraints=SelectionConstraints(
            min_quality=0.80, max_latency_ms=30_000, max_cost_usd=0.05,
            min_context_tokens=512, languages=["en"],
            notes="Region model must label question/answer regions, tables and "
                  "diagrams (plan Phase 4 OCR output schema).",
        ),
        primary=ModelCandidate(
            name="LayoutLMv3 base",
            hf_repo="microsoft/layoutlmv3-base",
            architecture="Multimodal Transformer (text + layout + image)",
            params="133M",
            modality="document image+text -> structured layout labels",
            input_format="page image + OCR text + bbox",
            output_format="layout/region labels per token/block",
            context_length="~512 tokens (fine-tuned on DocLayNet-like sets)",
            languages="en",
            reasoning="SOTA-ish document layout understanding for its size; the "
                      "standard open layout backbone",
            profile=ResourceProfile(
                params="133M", vram_gb="<2 (fp16)", ram_gb="4-6",
                cpu_feasible=True,
                quantization=["int8", "fp16"], framework="PyTorch / Transformers",
                license="CC BY-NC-SA 4.0", expected_latency="100-800 ms/page",
                non_commercial=True,
            ),
            limitations=["Non-commercial licence (must resolve for production); "
                         "needs OCR text+bbox as input, so it sits after the OCR "
                         "stage."],
            fallback_candidates=["Qwen/Qwen2.5-VL-7B-Instruct",
                                 "PaddlePaddle/PaddleOCR (PP-Structure)"],
        ),
        fallback=[
            ModelCandidate(
                name="Qwen2.5-VL-7B-Instruct (layout ad-hoc)",
                hf_repo="Qwen/Qwen2.5-VL-7B-Instruct",
                architecture="Qwen2.5 LLM + ViT vision encoder (VLM)",
                params="7B",
                modality="image+text -> text",
                input_format="full page image",
                output_format="JSON region descriptions",
                context_length="32,768",
                languages="multilingual",
                reasoning="Prompts full-page region analysis when a dedicated "
                          "layout model is unavailable",
                profile=ResourceProfile(
                    params="7B", vram_gb="~8 (int4) / ~16 (fp16)", ram_gb="12-20",
                    cpu_feasible=False,
                    quantization=["int4", "int8", "fp16"],
                    framework="PyTorch / Transformers",
                    license="Apache-2.0",
                    expected_latency="1-4 s/page on GPU",
                ),
                limitations=["Heavier; output schema must be prompt-enforced."],
                fallback_candidates=["OpenGVLab/InternVL2_5-8B"],
            ),
        ],
    ),
    "VISION_UNDERSTANDING": CapabilitySelection(
        capability="VISION_UNDERSTANDING",
        flag="vision.understanding",
        flags=["vision.understanding"],
        constraints=SelectionConstraints(
            min_quality=0.85, max_latency_ms=30_000, max_cost_usd=0.05,
            min_context_tokens=4096, languages=["en", "multilingual"],
            notes="Blanket visual-understanding capability; diagram and ID "
                  "capabilities specialise it.",
        ),
        primary=ModelCandidate(
            name="Qwen2.5-VL-7B-Instruct",
            hf_repo="Qwen/Qwen2.5-VL-7B-Instruct",
            architecture="Qwen2.5 LLM + ViT vision encoder (VLM)",
            params="7B",
            modality="image+text -> text",
            input_format="image(s), optional text",
            output_format="text / structured JSON",
            context_length="32,768",
            languages="multilingual",
            reasoning="Strong OCR+answer reading + grounded visual reasoning; "
                      "already in the HF model catalog",
            profile=ResourceProfile(
                params="7B", vram_gb="~8 (int4) / ~16 (fp16)", ram_gb="12-20",
                cpu_feasible=False,
                quantization=["int4", "int8", "fp16"],
                framework="PyTorch / Transformers",
                license="Apache-2.0", expected_latency="1-3 s/page on GPU",
            ),
            limitations=["Needs a GPU for acceptable latency."],
            fallback_candidates=["OpenGVLab/InternVL2_5-8B"],
        ),
        fallback=[
            ModelCandidate(
                name="InternVL 2.5 8B",
                hf_repo="OpenGVLab/InternVL2_5-8B",
                architecture="InternViT + Qwen2.5 LLM (VLM)",
                params="8B",
                modality="image+text -> text",
                input_format="image(s), optional text",
                output_format="text / structured JSON",
                context_length="~32,768 (8K native, longer via config)",
                languages="multilingual",
                reasoning="GPT-4o/Claude-class vision-language performance on "
                          "document/image benchmarks (2025 research)",
                profile=ResourceProfile(
                    params="8B", vram_gb="~8 (int4) / ~16 (fp16)", ram_gb="12-20",
                    cpu_feasible=False,
                    quantization=["int4", "int8", "fp16"],
                    framework="PyTorch / Transformers",
                    license="MIT", expected_latency="1-3 s/page on GPU",
                ),
                limitations=["Larger deploy; needs GPU."],
                fallback_candidates=["Qwen/Qwen2.5-VL-7B-Instruct"],
            ),
        ],
    ),
    "SEMANTIC_EMBEDDING": CapabilitySelection(
        capability="SEMANTIC_EMBEDDING",
        flag="semantic.embedding",
        flags=["semantic.embedding"],
        constraints=SelectionConstraints(
            min_quality=0.80, max_latency_ms=5_000, max_cost_usd=0.01,
            min_context_tokens=256, languages=["en", "multilingual"],
            notes="Local bi-encoder chosen over proprietary Google Embeddings 2 "
                  "(GE2 measured ~14x slower); retrieves candidate answer-key "
                  "concepts as evidence for the semantic evaluator.",
        ),
        primary=ModelCandidate(
            name="BGE-M3",
            hf_repo="BAAI/bge-m3",
            architecture="BERT-evolved bi-encoder (dense + sparse + ColBERT)",
            params="568M",
            modality="text -> vector",
            input_format="text (query or document)",
            output_format="1024-dim dense embedding (+ sparse)",
            context_length="8192 tokens",
            languages="100+ (multilingual)",
            reasoning="Multilingual retrieval/embedding SOTA for its size; "
                      "handles long answer texts",
            profile=ResourceProfile(
                params="568M", vram_gb="~2 (fp32) / ~1 (int8)", ram_gb="3-5",
                cpu_feasible=True,
                quantization=["int8"], framework="sentence-transformers / ONNX",
                license="MIT", expected_latency="5-50 ms/sentence on CPU",
            ),
            limitations=["Batch throughput on CPU modest for huge corpora."],
            fallback_candidates=["intfloat/multilingual-e5-large"],
        ),
        fallback=[
            ModelCandidate(
                name="Multilingual E5 large",
                hf_repo="intfloat/multilingual-e5-large",
                architecture="XLM-R bi-encoder",
                params="560M",
                modality="text -> vector",
                input_format="text (prefixed 'query:'/'passage:')",
                output_format="1024-dim dense embedding",
                context_length="512 tokens",
                languages="100",
                reasoning="Strong multilingual retrieval; the research-recommended "
                          "latency pick for local semantic matching",
                profile=ResourceProfile(
                    params="560M", vram_gb="~2 (fp32) / ~1 (int8)", ram_gb="3-5",
                    cpu_feasible=True,
                    quantization=["int8"], framework="sentence-transformers / ONNX",
                    license="MIT", expected_latency="5-50 ms/sentence on CPU",
                ),
                limitations=["512-token cap truncates long answers."],
                fallback_candidates=["BAAI/bge-m3"],
            ),
        ],
    ),
    "SEMANTIC_EVALUATION": CapabilitySelection(
        capability="SEMANTIC_EVALUATION",
        flag="semantic.answer_evaluation",
        flags=["semantic.answer_evaluation", "rubric.evaluation",
               "concept.extraction"],
        constraints=SelectionConstraints(
            min_quality=0.90, max_latency_ms=60_000, max_cost_usd=0.05,
            min_context_tokens=16_000, languages=["en", "multilingual"],
            notes="Evaluates meaning/met concepts, never exact wording; must emit "
                  "marks, satisfied/missing concepts, reasoning, confidence.",
        ),
        primary=ModelCandidate(
            name="Qwen3-30B-A3B-Instruct",
            hf_repo="Qwen/Qwen3-30B-A3B-Instruct",
            architecture="MoE LLM (~30B total / ~3.3B active)",
            params="~30B (A3B MoE)",
            modality="text -> text",
            input_format="question + student answer + answer key + rubric",
            output_format="structured JSON evaluation",
            context_length="40,960",
            languages="100+",
            reasoning="Cheap, fast MoE reasoning with tool calling; the default "
                      "chat model in the HF catalog",
            profile=ResourceProfile(
                params="30B/3.3B active", vram_gb="~18 (int4) / ~60 (fp16)",
                ram_gb="24-40", cpu_feasible=False,
                quantization=["int4", "int8"], framework="PyTorch / vLLM",
                license="Apache-2.0", expected_latency="1-4 s/answer on GPU",
            ),
            limitations=["Needs GPU; JSON schema prompt-enforced."],
            fallback_candidates=["Qwen/Qwen3-32B", "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B"],
        ),
        fallback=[
            ModelCandidate(
                name="Qwen3-32B (dense)",
                hf_repo="Qwen/Qwen3-32B",
                architecture="Dense LLM",
                params="32B",
                modality="text -> text",
                input_format="question + answer + key + rubric",
                output_format="structured JSON evaluation",
                context_length="~128K-ish family default (verify)",
                languages="multilingual",
                reasoning="Higher-capacity dense chat model for hard evaluations",
                profile=ResourceProfile(
                    params="32B", vram_gb="~20 (int4) / ~64 (fp16)", ram_gb="28-48",
                    cpu_feasible=False,
                    quantization=["int4", "int8"], framework="PyTorch / vLLM",
                    license="Apache-2.0", expected_latency="2-6 s/answer on GPU",
                ),
                limitations=["Heavier than the A3B primary."],
            ),
        ],
    ),
    "GENERAL_REASONING": CapabilitySelection(
        capability="GENERAL_REASONING",
        flag="reasoning.deep",
        flags=["reasoning.deep", "reasoning.shallow"],
        constraints=SelectionConstraints(
            min_quality=0.85, max_latency_ms=60_000, max_cost_usd=0.05,
            min_context_tokens=16_000, languages=["en", "multilingual"],
            notes="Backbone for normalisation, concept extraction and any "
                  "non-math reasoning node.",
        ),
        primary=ModelCandidate(
            name="Qwen3-30B-A3B-Instruct",
            hf_repo="Qwen/Qwen3-30B-A3B-Instruct",
            architecture="MoE LLM (~30B total / ~3.3B active)",
            params="~30B (A3B MoE)",
            modality="text -> text",
            input_format="text",
            output_format="text / JSON",
            context_length="40,960",
            languages="100+",
            reasoning="General reasoning + tool calling at MoE cost",
            profile=ResourceProfile(
                params="30B/3.3B active", vram_gb="~18 (int4) / ~60 (fp16)",
                ram_gb="24-40", cpu_feasible=False,
                quantization=["int4", "int8"], framework="PyTorch / vLLM",
                license="Apache-2.0", expected_latency="1-4 s/turn on GPU",
            ),
            limitations=["Needs GPU."],
            fallback_candidates=["meta-llama/Llama-3.3-70B-Instruct"],
        ),
        fallback=[
            ModelCandidate(
                name="Llama-3.3-70B-Instruct",
                hf_repo="meta-llama/Llama-3.3-70B-Instruct",
                architecture="Dense LLM",
                params="70B",
                modality="text -> text",
                input_format="text",
                output_format="text / JSON",
                context_length="128K",
                languages="multilingual",
                reasoning="Strong deep reasoning at scale; already in catalog",
                profile=ResourceProfile(
                    params="70B", vram_gb="~40 (int4) / ~140 (fp16)", ram_gb="48-80",
                    cpu_feasible=False,
                    quantization=["int4", "int8"], framework="PyTorch / vLLM",
                    license="Llama 3.3 Community License",
                    expected_latency="3-8 s/turn on GPU",
                ),
                limitations=["Heavy; licence terms apply; needs GPU."],
                fallback_candidates=["Qwen/Qwen3-30B-A3B-Instruct"],
            ),
        ],
    ),
    "MATHEMATICAL_REASONING": CapabilitySelection(
        capability="MATHEMATICAL_REASONING",
        flag="mathematical.evaluation",
        flags=["mathematical.evaluation"],
        constraints=SelectionConstraints(
            min_quality=0.85, max_latency_ms=90_000, max_cost_usd=0.05,
            min_context_tokens=4096, languages=["en"],
            notes="Step-marking requires the model to show formula/substitution/"
                  "calculation/units/final-answer work; a wrong final value must "
                  "not erase correct intermediate work.",
        ),
        primary=ModelCandidate(
            name="Qwen2.5-Math-7B-Instruct",
            hf_repo="Qwen/Qwen2.5-Math-7B-Instruct",
            architecture="Dense LLM, math-specialised",
            params="7B",
            modality="text -> text",
            input_format="problem/working text",
            output_format="step-by-step solution / JSON",
            context_length="~32K family default (verify)",
            languages="en, zh",
            reasoning="Math-specialised instruct model; high GSM/MATH500 accuracy "
                      "for its size (research roundup)",
            profile=ResourceProfile(
                params="7B", vram_gb="~15 (fp16) / ~7 (int4)", ram_gb="10-16",
                cpu_feasible=True,
                quantization=["int4", "int8", "fp16"], framework="PyTorch / vLLM",
                license="Apache-2.0", expected_latency="0.5-3 s/problem on GPU",
            ),
            limitations=["Math-focused, weaker on open-ended prose."],
            fallback_candidates=["deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"],
            verify_repo=True,
        ),
        fallback=[
            ModelCandidate(
                name="DeepSeek-R1-Distill-Qwen-7B",
                hf_repo="deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
                architecture="Dense LLM distilled from DeepSeek-R1 (Qwen2.5-Math base)",
                params="7B",
                modality="text -> text",
                input_format="problem/working text",
                output_format="step-by-step solution",
                context_length="32K family default (verify)",
                languages="en, zh",
                reasoning="R1-distill reasoning; MATH-500 74.6 (4bit benchmark "
                          "roundup); strong on competition math",
                profile=ResourceProfile(
                    params="7B", vram_gb="~14 (fp16) / ~6 (int4)", ram_gb="10-16",
                    cpu_feasible=True,
                    quantization=["int4", "int8", "fp16"], framework="PyTorch / vLLM",
                    license="MIT", expected_latency="0.5-3 s/problem on GPU",
                ),
                limitations=["Verbose CoT; slower per token."],
                fallback_candidates=["deepseek-ai/DeepSeekMath-RL-7B"],
                verify_repo=True,
            ),
            ModelCandidate(
                name="DeepSeekMath-RL-7B",
                hf_repo="deepseek-ai/DeepSeekMath-RL-7B",
                architecture="Dense LLM, math-specialised RL (GRPO)",
                params="7B",
                modality="text -> text",
                input_format="problem/working text",
                output_format="step-by-step solution",
                context_length="16K family default (verify)",
                languages="en, zh",
                reasoning="GSM8K 88.2 / MATH 51.7 without tools; 60.9 with "
                          "self-consistency (DeepSeek-Math paper)",
                profile=ResourceProfile(
                    params="7B", vram_gb="~14 (fp16) / ~6 (int4)", ram_gb="10-16",
                    cpu_feasible=True,
                    quantization=["int4", "int8", "fp16"], framework="PyTorch / vLLM",
                    license="MIT", expected_latency="0.5-3 s/problem on GPU",
                ),
                limitations=["Older (2024); fine for school-level math work."],
                fallback_candidates=["Qwen/Qwen2.5-Math-7B-Instruct"],
                verify_repo=True,
            ),
        ],
    ),
    "DIAGRAM_UNDERSTANDING": CapabilitySelection(
        capability="DIAGRAM_UNDERSTANDING",
        flag="diagram.evaluation",
        flags=["diagram.evaluation", "vision.understanding"],
        constraints=SelectionConstraints(
            min_quality=0.85, max_latency_ms=60_000, max_cost_usd=0.05,
            min_context_tokens=8192, languages=["en"],
            notes="Diagram questions need a VLM that reads the labelled figure "
                  "AND the question text together.",
        ),
        primary=ModelCandidate(
            name="Qwen2.5-VL-7B-Instruct",
            hf_repo="Qwen/Qwen2.5-VL-7B-Instruct",
            architecture="Qwen2.5 LLM + ViT vision encoder (VLM)",
            params="7B",
            modality="image+text -> text",
            input_format="diagram image + question text",
            output_format="structured evaluation / description",
            context_length="32,768",
            languages="multilingual",
            reasoning="Grounded visual reading of diagrams/figures",
            profile=ResourceProfile(
                params="7B", vram_gb="~8 (int4) / ~16 (fp16)", ram_gb="12-20",
                cpu_feasible=False,
                quantization=["int4", "int8", "fp16"],
                framework="PyTorch / Transformers",
                license="Apache-2.0", expected_latency="1-4 s/diagram on GPU",
            ),
            limitations=["Needs GPU."],
            fallback_candidates=["OpenGVLab/InternVL2_5-8B"],
        ),
        fallback=[
            ModelCandidate(
                name="InternVL 2.5 8B",
                hf_repo="OpenGVLab/InternVL2_5-8B",
                architecture="InternViT + Qwen2.5 LLM (VLM)",
                params="8B",
                modality="image+text -> text",
                input_format="diagram image + question text",
                output_format="structured evaluation / description",
                context_length="~32,768 (verify)",
                languages="multilingual",
                reasoning="Top-tier open VLM for image reasoning",
                profile=ResourceProfile(
                    params="8B", vram_gb="~8 (int4) / ~16 (fp16)", ram_gb="12-20",
                    cpu_feasible=False,
                    quantization=["int4", "int8", "fp16"],
                    framework="PyTorch / Transformers",
                    license="MIT", expected_latency="1-4 s/diagram on GPU",
                ),
                limitations=["Needs GPU."],
                fallback_candidates=["Qwen/Qwen2.5-VL-7B-Instruct"],
            ),
        ],
    ),
    "MULTILINGUAL_UNDERSTANDING": CapabilitySelection(
        capability="MULTILINGUAL_UNDERSTANDING",
        flag="text.normalization",
        flags=["text.normalization", "semantic.embedding"],
        constraints=SelectionConstraints(
            min_quality=0.80, max_latency_ms=20_000, max_cost_usd=0.05,
            min_context_tokens=4096, languages=["en", "hi", "ta", "te", "ml",
                                                "kn", "mr", "bn"],
            notes="When configured, answers in another language must be evaluated "
                  "on meaning. The reasoning LLM + multilingual embedder handle "
                  "this; a dedicated NMT stage is not required for the MVP.",
        ),
        primary=ModelCandidate(
            name="BGE-M3 (multilingual evidence) + Qwen3-30B-A3B (reasoning)",
            hf_repo="BAAI/bge-m3",  # plus Qwen/Qwen3-30B-A3B-Instruct
            architecture="bi-encoder + MoE LLM",
            params="568M + ~30B",
            modality="text -> vector / text",
            input_format="answer text in any supported language",
            output_format="embeddings + evaluation",
            context_length="8192 / 40,960",
            languages="100+",
            reasoning="Multilingual semantic retrieval plus multilingual "
                      "reasoning over the retrieved evidence",
            profile=ResourceProfile(
                params="~31B combined", vram_gb="~20 (embedder int8 + LLM int4)",
                ram_gb="28-45", cpu_feasible=False,
                quantization=["int8"], framework="sentence-transformers + vLLM",
                license="MIT + Apache-2.0",
                expected_latency="1-4 s/answer on GPU",
            ),
            limitations=["Needs GPU for the LLM half."],
            fallback_candidates=["intfloat/multilingual-e5-large"],
        ),
        fallback=[
            ModelCandidate(
                name="Multilingual E5 large",
                hf_repo="intfloat/multilingual-e5-large",
                architecture="XLM-R bi-encoder",
                params="560M",
                modality="text -> vector",
                input_format="text",
                output_format="1024-dim embedding",
                context_length="512 tokens",
                languages="100",
                reasoning="Retrieval fallback when BGE-M3 unavailable",
                profile=ResourceProfile(
                    params="560M", vram_gb="~2 (fp32) / ~1 (int8)", ram_gb="3-5",
                    cpu_feasible=True,
                    quantization=["int8"], framework="sentence-transformers",
                    license="MIT", expected_latency="5-50 ms/sentence on CPU",
                ),
                limitations=["512-token cap."],
                fallback_candidates=["BAAI/bge-m3"],
            ),
        ],
    ),
    "STUDENT_ID_EXTRACTION": CapabilitySelection(
        capability="STUDENT_ID_EXTRACTION",
        flag="student_id.extraction",
        flags=["student_id.extraction", "document.ocr"],
        constraints=SelectionConstraints(
            min_quality=0.90, max_latency_ms=30_000, max_cost_usd=0.05,
            min_context_tokens=4096, languages=["en"],
            notes="Must validate against the institutional roster when present "
                  "(plan Phase 5 identity validation); uncertain identity flags "
                  "IDENTITY_UNCERTAIN for review.",
        ),
        primary=ModelCandidate(
            name="Qwen2.5-VL-7B-Instruct (header reading)",
            hf_repo="Qwen/Qwen2.5-VL-7B-Instruct",
            architecture="Qwen2.5 LLM + ViT vision encoder (VLM)",
            params="7B",
            modality="image+text -> text",
            input_format="page header image",
            output_format="name/roll/register JSON",
            context_length="32,768",
            languages="multilingual",
            reasoning="Reads printed/handwritten header fields in context; "
                      "handles messy headers",
            profile=ResourceProfile(
                params="7B", vram_gb="~8 (int4) / ~16 (fp16)", ram_gb="12-20",
                cpu_feasible=False,
                quantization=["int4", "int8", "fp16"],
                framework="PyTorch / Transformers",
                license="Apache-2.0", expected_latency="0.5-3 s/header on GPU",
            ),
            limitations=["Needs GPU; roster validation is a downstream step."],
            fallback_candidates=["PaddlePaddle/PaddleOCR",
                                 "Qwen/Qwen3-30B-A3B-Instruct"],
        ),
        fallback=[
            ModelCandidate(
                name="PP-OCRv6 + Qwen3-30B-A3B (parse & validate)",
                hf_repo="PaddlePaddle/PaddleOCR",  # plus Qwen/Qwen3-30B-A3B-Instruct
                architecture="OCR pipeline + MoE LLM",
                params="~30M + ~30B",
                modality="image -> text -> structured fields",
                input_format="page header image",
                output_format="name/roll/register JSON",
                context_length="40,960 (LLM)",
                languages="100+",
                reasoning="Dedicated OCR for digits/labels + LLM normalisation "
                          "and roster-anchored validation",
                profile=ResourceProfile(
                    params="~31B combined", vram_gb="~19 (OCR int8 + LLM int4)",
                    ram_gb="24-45", cpu_feasible=False,
                    quantization=["int4", "int8"], framework="Paddle/PyTorch",
                    license="Apache-2.0", expected_latency="1-5 s/header on GPU",
                ),
                limitations=["Two-stage; needs GPU for the LLM."],
                fallback_candidates=["Qwen/Qwen2.5-VL-7B-Instruct"],
            ),
        ],
    ),
    "QUESTION_SEGMENTATION": CapabilitySelection(
        capability="QUESTION_SEGMENTATION",
        flag="question.segmentation",
        flags=["question.segmentation", "document.layout"],
        constraints=SelectionConstraints(
            min_quality=0.85, max_latency_ms=45_000, max_cost_usd=0.05,
            min_context_tokens=16_000, languages=["en"],
            notes="Must map out-of-order and continuation answers to question ids "
                  "and combine multi-page answers (plan Phase 5).",
        ),
        primary=ModelCandidate(
            name="Qwen3-30B-A3B-Instruct (mapping over structured OCR)",
            hf_repo="Qwen/Qwen3-30B-A3B-Instruct",
            architecture="MoE LLM",
            params="~30B (A3B MoE)",
            modality="text -> structured mapping",
            input_format="layout-labelled OCR blocks",
            output_format="question-id -> block/region JSON",
            context_length="40,960",
            languages="100+",
            reasoning="Reliable question-number parsing and merging against "
                      "layout-labelled OCR",
            profile=ResourceProfile(
                params="30B/3.3B active", vram_gb="~18 (int4) / ~60 (fp16)",
                ram_gb="24-40", cpu_feasible=False,
                quantization=["int4", "int8"], framework="PyTorch / vLLM",
                license="Apache-2.0", expected_latency="1-4 s/page on GPU",
            ),
            limitations=["Needs GPU; consumes layout output."],
            fallback_candidates=["Qwen/Qwen2.5-VL-7B-Instruct"],
        ),
        fallback=[
            ModelCandidate(
                name="Qwen2.5-VL-7B-Instruct (page-level mapping)",
                hf_repo="Qwen/Qwen2.5-VL-7B-Instruct",
                architecture="Qwen2.5 LLM + ViT vision encoder (VLM)",
                params="7B",
                modality="image+text -> text",
                input_format="page image + optional OCR",
                output_format="question-id -> region JSON",
                context_length="32,768",
                languages="multilingual",
                reasoning="Vision-grade mapping when layout labels are unreliable",
                profile=ResourceProfile(
                    params="7B", vram_gb="~8 (int4) / ~16 (fp16)", ram_gb="12-20",
                    cpu_feasible=False,
                    quantization=["int4", "int8", "fp16"],
                    framework="PyTorch / Transformers",
                    license="Apache-2.0", expected_latency="1-4 s/page on GPU",
                ),
                limitations=["Needs GPU."],
                fallback_candidates=["Qwen/Qwen3-30B-A3B-Instruct"],
            ),
        ],
    ),
    "EVALUATION_RECONCILIATION": CapabilitySelection(
        capability="EVALUATION_RECONCILIATION",
        flag="evaluation.reconciliation",
        flags=["evaluation.reconciliation", "semantic.answer_evaluation"],
        constraints=SelectionConstraints(
            min_quality=0.90, max_latency_ms=90_000, max_cost_usd=0.05,
            min_context_tokens=16_000, languages=["en"],
            notes="Compares two independent agent evaluations, classifies "
                  "disagreement, and produces the final mark + confidence + "
                  "review reason (plan Phase 7/9).",
        ),
        primary=ModelCandidate(
            name="DeepSeek-R1-Distill-Qwen-32B",
            hf_repo="deepseek-ai/DeepSeek-R1-Distill-Qwen-32B",
            architecture="Dense LLM distilled from DeepSeek-R1",
            params="32B",
            modality="text -> text",
            input_format="two agent evaluations + key + rubric",
            output_format="final mark / confidence / review reason",
            context_length="not stated (R1-distill family)",
            languages="en, zh",
            reasoning="Documented AIME 72.6 / MATH-500 90 reasoning strength -- "
                      "the kind of self-checking needed to arbitrate two agents",
            profile=ResourceProfile(
                params="32B", vram_gb="~20 (int4) / ~64 (fp16)", ram_gb="28-48",
                cpu_feasible=False,
                quantization=["int4", "int8"], framework="PyTorch / vLLM",
                license="MIT", expected_latency="2-8 s/decision on GPU",
            ),
            limitations=["Verbose CoT; needs GPU."],
            fallback_candidates=["Qwen/Qwen3-30B-A3B-Instruct"],
        ),
        fallback=[
            ModelCandidate(
                name="Qwen3-30B-A3B-Instruct",
                hf_repo="Qwen/Qwen3-30B-A3B-Instruct",
                architecture="MoE LLM (~30B total / ~3.3B active)",
                params="~30B (A3B MoE)",
                modality="text -> text",
                input_format="two agent evaluations + key + rubric",
                output_format="final mark / confidence / review reason",
                context_length="40,960",
                languages="100+",
                reasoning="Fast, instruction-grade arbitration fallback",
                profile=ResourceProfile(
                    params="30B/3.3B active", vram_gb="~18 (int4) / ~60 (fp16)",
                    ram_gb="24-40", cpu_feasible=False,
                    quantization=["int4", "int8"], framework="PyTorch / vLLM",
                    license="Apache-2.0", expected_latency="1-5 s/decision on GPU",
                ),
                limitations=["Weaker self-checking than R1 for hard disputes."],
                fallback_candidates=["meta-llama/Llama-3.3-70B-Instruct"],
            ),
        ],
    ),
}


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def selection_for_flag(flag: str) -> Optional[CapabilitySelection]:
    """Primary/fallback selection for a capability flag (any of its flags)."""
    for capability, selection in EXAM_MODEL_SELECTION.items():
        if flag in selection.flags:
            return selection
    return None


def selection_for_capability(capability: str) -> Optional[CapabilitySelection]:
    """Selection keyed by the plan capability name (e.g. 'DOCUMENT_OCR')."""
    return EXAM_MODEL_SELECTION.get(capability)


def flags_for_capability(capability: str) -> Optional[List[str]]:
    """Capability flags a plan capability maps onto, if present."""
    return PLAN_CAPABILITY_FLAGS.get(capability)