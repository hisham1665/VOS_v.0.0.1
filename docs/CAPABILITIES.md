# CAPABILITIES.md — All Capability Implementations

## Convention

Every capability follows:

```python
def run(text: str, instruction: Optional[str] = None) -> str
```

- `text`: Primary input (query, file path, or upstream concatenated output)
- `instruction`: The node's description from the DAG (augments behavior)
- Returns: Result string, or `NO_DATA: <reason>` sentinel on failure

## Default Capabilities (capabilities/)

### web_search

**File:** `capabilities/web_search.py`
**Model:** `qwen/qwen3.6-27b` via Groq
**Flow:** DuckDuckGo search → Groq fact extraction

| Aspect | Detail |
|--------|--------|
| Input text | Search query or entity name |
| Instruction | Used as search query when present |
| Search | `DDGS().text(query, max_results=8)` |
| Extraction | Groq chat completion, temp=0.3, max_tokens=2048 |
| System prompt | Research assistant: extract ALL facts/numbers/dates, preserve enumeration, NO_DATA if no results |
| Empty results | Returns `NO_DATA: web search returned no results for ...` |
| Search errors | Returns error text (not raised) for FailureManager classification |

### summarization

**File:** `capabilities/summarization.py`
**Model:** `qwen/qwen3.6-27b` via Groq

| Aspect | Detail |
|--------|--------|
| Input text | Source text to summarize |
| Instruction | Appended to system prompt as "THIS SUBTASK: ..." |
| System prompt | Fact-preserving: ALL facts/numbers/dates/names preserved, no vague words when exact numbers exist |
| Call | temp=0.3, max_tokens=2048 |

### vision

**File:** `capabilities/vision.py`
**Model:** `medgemma1.5:latest` via Ollama (localhost:11434)

| Aspect | Detail |
|--------|--------|
| Input text | Image file path |
| Instruction | Used as prompt, defaults to "Describe this image in detail." |
| Encoding | Base64 data URL with detected MIME type |
| Call | OpenAI-compatible endpoint, temp=0.3, max_tokens=1024 |
| Missing file | Raises `FileNotFoundError` with usage hint |

### synthesis

**File:** `capabilities/synthesis.py`
**Model:** `qwen/qwen3.6-27b` via Groq

| Aspect | Detail |
|--------|--------|
| Input text | Concatenated upstream research outputs |
| Instruction | Original user prompt (by convention) |
| System prompt | 5 rules: answer every part, respect quantities, preserve specifics, flag gaps, flag unverified |
| Call | temp=0.2, max_tokens=4096 (generous — final answer) |
| Purpose | Compose final answer, NOT summarize (distinct from summarization) |

### document

**File:** `capabilities/document.py`
**Library:** `pdfplumber`

| Aspect | Detail |
|--------|--------|
| Input text | PDF file path |
| Extraction | Per-page `page.extract_text()`, joins with `\n\n` |
| Missing file | Raises `FileNotFoundError` |
| Empty PDF | Returns `NO_DATA: PDF contained no extractable text...` |
| Instruction | Accepted for interface parity, doesn't change output |

## HuggingFace Capabilities (providers/hf.py)

### Model Catalog (17 models)

| Resource ID | Model | Class | Interface | Transport |
|-------------|-------|-------|-----------|-----------|
| `hf_qwen3_30b_a3b_instruct` | Qwen3-30B-A3B-Instruct | llm | chat_completion | wired |
| `hf_qwen3_8b` | Qwen3-8B | llm | chat_completion | wired |
| `hf_qwen2_5_vl_7b_instruct` | Qwen2.5-VL-7B-Instruct | vlm | chat_completion | wired |
| `hf_qwen3_coder_30b_a3b` | Qwen3-Coder-30B-A3B | llm | chat_completion | wired |
| `hf_deepseek_r1_distill_qwen_32b` | deepseek-r1-distill-qwen-32b | llm | chat_completion | wired |
| `hf_llama3_3_70b_instruct` | Llama-3.3-70B-Instruct | llm | chat_completion | wired |
| `hf_llama3_2_3b_instruct` | Llama-3.2-3B-Instruct | llm | chat_completion | wired |
| `hf_gemma2_27b_it` | gemma-2-27b-it | llm | chat_completion | wired |
| `hf_bge_large_en_v1_5` | bge-large-en-v1.5 | embedder | feature_extraction | declared |
| `hf_bge_reranker_v2_m3` | bge-reranker-v2-m3 | reranker | rerank | declared |
| `hf_whisper_large_v3_turbo` | whisper-large-v3-turbo | asr | automatic_speech_recognition | wired |
| `hf_whisper_large_v3` | whisper-large-v3 | asr | automatic_speech_recognition | wired |
| `hf_qwen2_audio_7b_instruct` | Qwen2-Audio-7B | audio | audio_chat_completion | wired |
| `hf_ast_audioset_finetuned` | MIT/AST | audio | audio_classification | wired |
| `hf_siglip2_base_224` | siglip2-base-patch16-224 | image | zero_shot_image_classification | declared |
| `hf_yolo11` | YOLO11 | image | object_detection | declared |
| `hf_dinov3_vitb16` | dinov3-vitb16 | image | image_feature_extraction | declared |
| `hf_medgemma_1_5_4b_it` | medgemma-1.5-4b-it | vlm | image_chat_completion | wired |
| `hf_healthcare_brain_lab_ner` | healthcare-brain-laboratory-ner | api | token_classification | wired |

**Transport:** `"wired"` = executable via run_fn; `"declared"` = raises `ProviderError` on invocation (proves capability-based selection).

### HF Run Functions

```python
def _chat_run(text, instruction=None, *, provider, system, temperature, max_tokens) -> str
def _asr_run(text, instruction=None, *, provider, model) -> str
def _audio_chat_run(text, instruction=None, *, provider, model, system, temperature, max_tokens) -> str
def _audio_classification_run(text, instruction=None, *, provider, model) -> str
def _image_chat_run(text, instruction=None, *, provider, model, system, temperature, max_tokens) -> str
def _token_classification_run(text, instruction=None, *, provider, model) -> str
```

## Medical Capabilities (medical/capabilities.py)

Seven capabilities registered via `medical/registration.py`:

### medical_folder_ingestion

Scans a patient folder, emits `FileManifest` block.

### medical_document_analysis

Extracts observations and patient demographics from clinical documents.

### medical_laboratory_analysis

Extracts lab values using NER (with deterministic fallback). Status derived from report's own reference ranges.

### medical_image_analysis

MedGemma visual description. Each finding flagged "Requires physician/radiologist confirmation".

### medical_prescription_analysis

Regex extraction of diagnoses and medications (name, dose, frequency).

### medical_report_analysis

Extracts conditions, observations, and demographics from discharge summaries.

### medical_patient_synthesis

Deterministic merge of all analysis nodes into a structured Patient Chart with source attribution.

## Default Resource Pool

| Resource ID | Class | Key Capabilities | Cost | p50/p95 Latency |
|-------------|-------|------------------|------|-----------------|
| `web_search` | tool | web.search, tool.calling, reasoning.shallow | $0.004 | 3000/9000ms |
| `summarization` | llm | text.summarization, reasoning.deep/shallow | $0.003 | 1500/5000ms |
| `vision` | vlm | vision.understanding, reasoning.shallow | $0.000 | 6000/15000ms |
| `document_extraction` | tool | document.extraction, text.summarization | $0.000 | 300/2000ms |
| `synthesis` | llm | answer.synthesis, reasoning.deep/shallow | $0.006 | 3500/9000ms |
| `quick_summarization` | llm | text.summarization, text.classification | $0.001 | 700/2200ms |

## NO_DATA Sentinel Contract

Capabilities emit `NO_DATA: <reason>` when:
- Search returns no results
- PDF has no extractable text
- Model returns empty response

This sentinel is detected by the FailureManager's content classifier and triggers the recovery ladder.
