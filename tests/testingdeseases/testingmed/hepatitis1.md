# Preliminary Imaging Report -- uploads-814423f7

> ⚠️ **AI-generated draft for clinician review. This report is decision *support* only: it is not a diagnosis, has not been validated for clinical use, and must be verified by a qualified clinician (and a radiologist for imaging) before it informs patient care.**

## Patient status

| | |
|---|---|
| **Overall** | 🟢 NORMAL |
| **Triage** | **ROUTINE** -- review at the next scheduled opportunity. |
| **Images** | 0 (unknown) |
| **Documents** | 9 |
| **Generated** | 2026-10-05 12:36:52 UTC in 67.9s |

Synthesis unavailable; 1 finding(s) carried forward.

### Recommended next steps
1. Manual radiologist review required.

## Clinical details

| Field | Value |
|---|---|
| Age | _not supplied_ |
| Sex | _not supplied_ |
| History | feel unusually tired, have pain in the upper right side of their belly, and have noticed dark urine or yellow skin. |
| Referring question | _not supplied_ |
| Indication | Not available (report synthesis unavailable). |
| Technique | See study metadata. |

## Findings

| Severity | Finding | Location | Confidence | Seen on | Document sources |
|---|---|---|---|---|---|
| 🟢 NORMAL | **No acute abnormality** | - | 100% | 0 | - |

- **No acute abnormality** -- All laboratory values, urinalysis, coagulation studies, hepatitis screening, and the limited MRI assessment are within normal reference ranges; no focal hepatic lesion identified.

## Impression

Automated impression could not be generated (BadRequestError). Findings extracted upstream: No acute abnormality. This study requires direct radiologist review.

## Documents analysed

| Document | Format | Pages | Source | Warnings |
|---|---|---:|---|---|
| `01_Liver_Function_Test.pdf` | PDF | 1 | `01_Liver_Function_Test.pdf` | - |
| `02_CBC.pdf` | PDF | 1 | `02_CBC.pdf` | - |
| `03_Renal_Function_Electrolytes.pdf` | PDF | 1 | `03_Renal_Function_Electrolytes.pdf` | - |
| `04_Coagulation_Profile (2).pdf` | PDF | 1 | `04_Coagulation_Profile (2).pdf` | - |
| `05_Alpha_Fetoprotein.pdf` | PDF | 1 | `05_Alpha_Fetoprotein.pdf` | - |
| `06_Hepatitis_Screening.pdf` | PDF | 1 | `06_Hepatitis_Screening.pdf` | - |
| `07_Urinalysis.pdf` | PDF | 1 | `07_Urinalysis.pdf` | - |
| `08_MRI_Liver_Report.pdf` | PDF | 1 | `08_MRI_Liver_Report.pdf` | - |
| `09_Kidney_Liver_Panel_Summary.pdf` | PDF | 1 | `09_Kidney_Liver_Panel_Summary.pdf` | - |

### Extracted document reads

#### `01_Liver_Function_Test.pdf#page=1`

{
  "document_type": "lab_report",
  "findings": [
    {
      "label": "Total Bilirubin",
      "description": "0.8 mg/dL (reference range 0.2–1.2 mg/dL)",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Direct Bilirubin",
      "description": "0.2 mg/dL (reference range 0.0–0.3 mg/dL)",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "AST",
      "description": "28 U/L (reference range 10–40 U/L)",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "ALT",
      "description": "31 U/L (reference range 7–56 U/L)",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Alkaline Phosphatase (ALP)",
      "description": "96 U/L (reference range 44–147 U/L)",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "GGT",
      "description": "42 U/L (reference range 9–48 U/L)",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Albumin",
      "description": "4.0 g/dL (reference range 3.5–5.0 g/dL)",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Total Protein",
      "description": "7.1 g/dL (reference range 6.0–8.3 g/dL)",
      "severity": "normal",
      "confidence": 1.0
    }
  ],
  "quality_issues": [
    "Missing patient identifiers (name, age, sex)",
    "Inconsistent examination description (Liver Function Test listed under 'Examination Liver / Upper-abdominal MRI')"
  ]
}

#### `02_CBC.pdf#page=1`

{
  "document_type": "blood_report",
  "findings": [
    {
      "label": "Hemoglobin",
      "description": "13.6 g/dL (reference range 12.0–16.0)",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "White Blood Cell Count",
      "description": "7,800 /µL (reference range 4,000–11,000)",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Platelet Count",
      "description": "248,000 /µL (reference range 150,000–450,000)",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Hematocrit",
      "description": "40.2% (reference range 36–46)",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Neutrophils",
      "description": "61% (reference range 40–75)",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Lymphocytes",
      "description": "30% (reference range 20–45)",
      "severity": "normal",
      "confidence": 1.0
    }
  ],
  "quality_issues": [
    "Missing patient identifiers (name, age, sex) and clinical history on the report"
  ]
}

#### `03_Renal_Function_Electrolytes.pdf#page=1`

{
  "document_type": "lab_report",
  "findings": [
    {
      "label": "Urea",
      "description": "Urea 26 mg/dL (reference range 15–40 mg/dL)",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Creatinine",
      "description": "Creatinine 0.86 mg/dL (reference range 0.6–1.1 mg/dL)",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "eGFR",
      "description": "eGFR >90 mL/min/1.73m² (reference >60 mL/min/1.73m²)",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Sodium",
      "description": "Sodium 140 mmol/L (reference range 135–145 mmol/L)",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Potassium",
      "description": "Potassium 4.2 mmol/L (reference range 3.5–5.1 mmol/L)",
      "severity": "normal",
      "confidence": 1.0
    }
  ],
  "quality_issues": [
    "Missing patient demographics (age, sex) and clinical history on the report"
  ]
}

#### `04_Coagulation_Profile (2).pdf#page=1`

{
  "document_type": "lab_report",
  "findings": [
    {
      "label": "Prothrombin Time (PT)",
      "description": "12.4 sec (reference range 11–14 sec)",
      "severity": "normal",
      "confidence": 0.99
    },
    {
      "label": "International Normalized Ratio (INR)",
      "description": "1.02 (reference range 0.8–1.2)",
      "severity": "normal",
      "confidence": 0.99
    },
    {
      "label": "Activated Partial Thromboplastin Time (aPTT)",
      "description": "30 sec (reference range 25–35 sec)",
      "severity": "normal",
      "confidence": 0.99
    }
  ],
  "quality_issues": [
    "patient identifiers (name, age, sex) not provided"
  ]
}

#### `05_Alpha_Fetoprotein.pdf#page=1`

{
  "document_type": "lab_report",
  "findings": [
    {
      "label": "Alpha-fetoprotein (AFP) level",
      "description": "AFP 4.8 ng/mL (reference range <10 ng/mL); reported as within normal limits",
      "severity": "normal",
      "confidence": 1.0
    }
  ],
  "quality_issues": [
    "patient demographics and clinical history not provided on the page"
  ]
}

#### `06_Hepatitis_Screening.pdf#page=1`

{
  "document_type": "lab_report",
  "findings": [
    {
      "label": "HBsAg",
      "description": "Non-reactive",
      "severity": "normal",
      "confidence": 0.99
    },
    {
      "label": "Anti-HCV antibody",
      "description": "Non-reactive",
      "severity": "normal",
      "confidence": 0.99
    },
    {
      "label": "Anti-HAV IgM",
      "description": "Negative",
      "severity": "normal",
      "confidence": 0.99
    }
  ],
  "quality_issues": [
    "patient demographics and clinical history not provided on the page"
  ]
}

#### `07_Urinalysis.pdf#page=1`

{
  "document_type": "lab_report",
  "findings": [
    {
      "label": "Urine colour",
      "description": "Pale yellow",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Urine appearance",
      "description": "Clear",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Urine pH",
      "description": "6.0",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Specific gravity",
      "description": "1.015",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Protein",
      "description": "Negative",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Glucose",
      "description": "Negative",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Ketones",
      "description": "Negative",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Blood (hematuria)",
      "description": "Negative",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "White blood cells (WBC)",
      "description": "0–2 /HPF",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Red blood cells (RBC)",
      "description": "0–1 /HPF",
      "severity": "normal",
      "confidence": 1.0
    }
  ],
  "quality_issues": [
    "Patient demographics (name, age, sex) not provided",
    "Reference ranges not supplied for many parameters"
  ]
}

#### `08_MRI_Liver_Report.pdf#page=1`

{
  "document_type": "mri",
  "findings": [
    {
      "label": "No definitive focal hepatic lesion",
      "description": "The single uploaded axial MRI image does not allow reliable characterization of a focal liver lesion; no definitive focal hepatic lesion can be diagnosed.",
      "severity": "normal",
      "confidence": 1.0
    }
  ],
  "quality_issues": [
    "Limited single-image assessment; incomplete MRI study prevents full evaluation"
  ]
}

#### `09_Kidney_Liver_Panel_Summary.pdf#page=1`

{
  "document_type": "lab_report",
  "findings": [
    {
      "label": "Liver enzymes",
      "description": "Within reference range",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Bilirubin",
      "description": "Within reference range",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Albumin / protein",
      "description": "Within reference range",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Renal function",
      "description": "Within reference range",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Electrolytes",
      "description": "Within reference range",
      "severity": "normal",
      "confidence": 1.0
    },
    {
      "label": "Coagulation",
      "description": "Within reference range",
      "severity": "normal",
      "confidence": 1.0
    }
  ],
  "quality_issues": [
    "sample data, not a real patient",
    "no specific numeric values or reference ranges provided"
  ]
}

## Warnings

- Image quality -- Missing patient identifiers (name, age, sex) in multiple reports
- Image quality -- Inconsistent examination description (Liver Function Test listed under MRI)
- Image quality -- Reference ranges not supplied for many urinalysis parameters
- Image quality -- Limited single-image MRI assessment; incomplete study prevents full evaluation
- Image quality -- Sample data in Kidney_Liver_Panel_Summary; no specific numeric values or reference ranges provided

## Run diagnostics

| Stage | Capability | Status | Time | Detail |
|---|---|---|---|---|
| `document0` | document_analysis | ✅ done | 5.00s | 2836 chars |
| `document1` | document_analysis | ✅ done | 2.57s | 1984 chars |
| `document2` | document_analysis | ✅ done | 2.37s | 1973 chars |
| `document3` | document_analysis | ✅ done | 1.82s | 1361 chars |
| `document4` | document_analysis | ✅ done | 1.67s | 803 chars |
| `document5` | document_analysis | ✅ done | 1.67s | 1105 chars |
| `document6` | document_analysis | ✅ done | 2.47s | 2628 chars |
| `document7` | document_analysis | ✅ done | 1.67s | 978 chars |
| `document8` | document_analysis | ✅ done | 16.16s | 2038 chars |
| `consolidate` | cross_image_synthesis | ✅ done | 45.25s | 662 chars |
| `report` | report_synthesis | ✅ done | 4.60s | 338 chars |

### Models used

- `cross_image_synthesis` -> `openai/gpt-oss-120b`
- `document_analysis` -> `openai/gpt-oss-120b`

---

_AI-generated draft for clinician review. This report is decision *support* only: it is not a diagnosis, has not been validated for clinical use, and must be verified by a qualified clinician (and a radiologist for imaging) before it informs patient care._ Report generated by Medical AOS at 2026-10-05 12:36:52 UTC.
