# MEDICAL_EXTENSION.md — Medical AOS Extension

## Overview

The Medical AOS extension is a **doctor-assistant workflow** built additively on top of the standard AOS kernel. It introduces 7 medical capabilities without touching any existing planning, DNA, routing, or execution algorithm.

## Architecture

```
User Prompt + --medical-folder
         │
         ▼
  scan_folder() → FileManifest[]
         │
         ▼
  build_medical_job() → job prompt with embedded manifest
         │
         ▼
  register_medical_resources() → adds 7 capabilities to registry
         │
         ▼
  Standard AOS pipeline (plan → DNA → constraints → select → execute → integrate)
         │
         ▼
  Patient Chart (Markdown)
```

**Key insight:** The kernel doesn't know it's running medical — medical capabilities are ordinary manifests registered alongside the default pool.

## Medical Capabilities

### 1. medical_folder_ingestion

**File:** `medical/capabilities.py::medical_folder_ingestion_run`

| Aspect | Detail |
|--------|--------|
| Input | Folder path (from job prompt FOLDER: hint) |
| Action | `scan_folder()` recursively classifies all files |
| Output | `FileManifest` block wrapped in `【MEDICAL:manifest】...【/MEDICAL】` markers |
| Categories | laboratory_report, radiology (xray/ct/mri/scan), prescription, discharge_summary, medical_report, clinical_document, unsupported |

### 2. medical_document_analysis

**File:** `medical/capabilities.py::medical_document_analysis_run`

| Aspect | Detail |
|--------|--------|
| Input | Manifest block with medical/clinical documents |
| Action | Extracts text via pdfplumber, collects lines ≥20 chars as observations |
| Output | `PatientProfile` + `MedicalObservation` blocks |
| Cap | 120 observations max |

### 3. medical_laboratory_analysis

**File:** `medical/capabilities.py::medical_laboratory_analysis_run`

| Aspect | Detail |
|--------|--------|
| Input | Manifest block with laboratory_report entries |
| Action | NER first (`healthcare-brain-laboratory-ner`), deterministic fallback (`parse_lab_lines`) |
| Output | `LabResult` blocks |
| Status derivation | From report's own reference ranges, never from external knowledge |
| De-dup | On (test, source_file, page) tuple |

### 4. medical_image_analysis

**File:** `medical/capabilities.py::medical_image_analysis_run`

| Aspect | Detail |
|--------|--------|
| Input | Manifest block with image entries |
| Action | MedGemma (`google/medgemma-1.5-4b-it`) visual description per image |
| Output | `ImagingFinding` blocks |
| Safety | Every finding flagged "Requires physician/radiologist confirmation" |
| Cap | 600 chars per finding |

### 5. medical_prescription_analysis

**File:** `medical/capabilities.py::medical_prescription_analysis_run`

| Aspect | Detail |
|--------|--------|
| Input | Manifest block with prescription entries |
| Action | Regex extraction of diagnoses and medications |
| Output | `Medication` + `MedicalCondition` blocks |
| Frequency patterns | q\d+h, daily, bid, tid, qid, prn, etc. |

### 6. medical_report_analysis

**File:** `medical/capabilities.py::medical_report_analysis_run`

| Aspect | Detail |
|--------|--------|
| Input | Manifest block with medical_report/discharge_summary entries |
| Action | Extracts diagnoses from "discharge diagnosis/impression" lines |
| Output | `MedicalCondition` + `MedicalObservation` + `PatientProfile` blocks |
| Cap | 200 chars per observation |

### 7. medical_patient_synthesis

**File:** `medical/capabilities.py::medical_patient_synthesis_run`

| Aspect | Detail |
|--------|--------|
| Input | All upstream analysis blocks |
| Action | Deterministic assembly (NO LLM call) |
| Output | Full Markdown Patient Chart |
| Sections | Profile, Lab Values table, Imaging Findings table, Medications table, Conditions table, Clinical Observations, Unsupported Files |
| Safety | Disclaimer: "is NOT a medical opinion", "Review with a qualified clinician" |

## Data Structures

### FileManifest

```python
class FileManifest(BaseModel):
    filename: str
    relative_path: str
    extension: str
    mime_type: str
    file_size: int
    detected_type: str       # "pdf"|"image"|"text"|"unknown"
    processing_status: str   # "pending"|"processed"|"error"
    category: str            # Classification from scan_folder
    absolute_path: str
    page_count: Optional[int]
    diagnosis: Optional[str]
    notes: Optional[str]
```

### LabResult

```python
class LabResult(BaseModel):
    test: str                # "Glucose"
    value: str               # "142"
    unit: str                # "mg/dL"
    reference_range: str     # "70-100"
    status: str              # "HIGH"|"LOW"|"NORMAL"
    source_file: str
    page: Optional[int]
    source_type: str
    note: Optional[str]
```

### ImagingFinding

```python
class ImagingFinding(BaseModel):
    finding: str
    source: str
    evidence: str
    status: str  # Always includes physician confirmation requirement
```

### Medication

```python
class Medication(BaseModel):
    medicine: str
    dose: Optional[str]
    frequency: Optional[str]
    duration: Optional[str]
    source: str
    page: Optional[int]
    source_type: str
    note: Optional[str]
```

## Block Protocol

All medical capabilities communicate via marker-wrapped blocks:

```
【MEDICAL:manifest】{"filename": "blood.pdf", ...}【/MEDICAL】
【MEDICAL:lab】{"test": "Glucose", "value": "142", ...}【/MEDICAL】
【MEDICAL:imaging】{"finding": "...", ...}【/MEDICAL】
【MEDICAL:medication】{"medicine": "Amoxicillin", ...}【/MEDICAL】
【MEDICAL:condition】{"condition": "pneumonia", ...}【/MEDICAL】
【MEDICAL:patient】{"name": "John", ...}【/MEDICAL】
【MEDICAL:observation】{"observation": "...", ...}【/MEDICAL】
```

Parsing: `manifest.parse_blocks(text)` → `Dict[str, list]`

## Workflow Entry

```bash
python -m aos_v0 --medical-folder data/inputs/patient1 "Build a chart from these labs"
```

### What happens:

1. `scan_folder()` pre-scans the folder
2. `build_medical_job()` embeds the manifest + MEDICAL WORKFLOW directive in the prompt
3. `register_medical_resources()` adds 7 capabilities to the registry
4. Standard pipeline runs — ManagerAgent sees the directive and plans accordingly
5. DNAExtractor, ConstraintPolicy, CapabilityRegistry, GraphExecutor all work normally
6. Medical capabilities are invoked like any other capability

## Safety Behaviors

| Rule | Enforcement |
|------|-------------|
| Imaging findings require physician confirmation | Every `ImagingFinding.status` contains confirmation text |
| Lab statuses from report only | `parse_lab_lines` derives from own reference ranges |
| Source attribution | Every row in Patient Chart names source file and page |
| No medical opinion | Chart includes disclaimer; synthesis is deterministic aggregation |
| Graceful degradation | Missing HF_TOKEN triggers NER fallback to deterministic parser |

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `MEDICAL_IMAGE_MODEL` | `google/medgemma-1.5-4b-it` | Vision model for imaging |
| `MEDICAL_LAB_MODEL` | `genzeonplatform/healthcare-brain-laboratory-ner` | NER model for lab values |
| `HF_TOKEN` | — | Required for medical HF models |

## File Classification Rules

### Images (extension-based)
- `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.bmp`, `.tiff` → `category: "image"`

### Image sub-classification (filename keywords)
- xray, x-ray → `radiology/xray`
- ct, computed → `radiology/ct_scan`
- mri → `radiology/mri`
- scan (generic) → `radiology/scan`

### Documents (extension-based)
- `.pdf`, `.doc`, `.docx`, `.txt`, `.md` → `category: "document"`

### Document sub-classification (filename keywords)
- lab, laboratory, blood, urine → `laboratory_report`
- prescription, rx, medicine → `prescription`
- discharge → `discharge_summary`
- report (generic) → `medical_report`
- clinical, note → `clinical_document`
