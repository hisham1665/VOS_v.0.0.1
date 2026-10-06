# Preliminary Imaging Report -- uploads-7478998d

> ⚠️ **AI-generated draft for clinician review. This report is decision *support* only: it is not a diagnosis, has not been validated for clinical use, and must be verified by a qualified clinician (and a radiologist for imaging) before it informs patient care.**

> 🟠 **DEGRADED RUN** -- one or more analysis stages did not complete. This report is incomplete; see *Run diagnostics*.

## Patient status

| | |
|---|---|
| **Overall** | ⚪ INDETERMINATE |
| **Triage** | **ROUTINE** -- review at the next scheduled opportunity. |
| **Images** | 0 (unknown) |
| **Documents** | 13 |
| **Generated** | 2026-10-05 12:54:57 UTC in 9.4s |

Status synthesis did not complete; severity was derived from the extracted findings alone.

### Recommended next steps
1. Manual radiologist review required.

## Clinical details

| Field | Value |
|---|---|
| Age | _not supplied_ |
| Sex | _not supplied_ |
| History | sudden  confusing physical changes in plain |
| Referring question | _not supplied_ |
| Indication | sudden  confusing physical changes in plain |
| Technique | 0 image(s) and 13 document(s), unspecified imaging. |

## Findings

_No findings were extracted. See run diagnostics below._

## Impression

No interpretation could be produced for this study. Every analysis stage failed; see the warnings section.

## Documents analysed

| Document | Format | Pages | Source | Warnings |
|---|---|---:|---|---|
| `01_CBC (3).pdf` | PDF | 1 | `01_CBC (3).pdf` | - |
| `02_Random_Blood_Glucose.pdf` | PDF | 1 | `02_Random_Blood_Glucose.pdf` | - |
| `03_HbA1c.pdf` | PDF | 1 | `03_HbA1c.pdf` | - |
| `04_Renal_Function_Electrolytes.pdf` | PDF | 1 | `04_Renal_Function_Electrolytes.pdf` | - |
| `05_Coagulation_Profile.pdf` | PDF | 1 | `05_Coagulation_Profile.pdf` | - |
| `06_Lipid_Profile.pdf` | PDF | 1 | `06_Lipid_Profile.pdf` | - |
| `07_CT_Brain_Without_Contrast.pdf` | PDF | 1 | `07_CT_Brain_Without_Contrast.pdf` | - |
| `08_MRI_Brain_DWI_ADC.pdf` | PDF | 1 | `08_MRI_Brain_DWI_ADC.pdf` | - |
| `09_CT_Angiography_Head_Neck.pdf` | PDF | 1 | `09_CT_Angiography_Head_Neck.pdf` | - |
| `10_Carotid_Doppler.pdf` | PDF | 1 | `10_Carotid_Doppler.pdf` | - |
| `11_ECG.pdf` | PDF | 1 | `11_ECG.pdf` | - |
| `12_Echocardiogram.pdf` | PDF | 1 | `12_Echocardiogram.pdf` | - |
| `13_Troponin_I.pdf` | PDF | 1 | `13_Troponin_I.pdf` | - |

## Warnings

- Pipeline stage 'document0' (document_analysis) failed: [document_analysis] every document page failed -> 01_CBC (3).pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined
- Pipeline stage 'document1' (document_analysis) failed: [document_analysis] every document page failed -> 02_Random_Blood_Glucose.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined
- Pipeline stage 'document2' (document_analysis) failed: [document_analysis] every document page failed -> 03_HbA1c.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined
- Pipeline stage 'document3' (document_analysis) failed: [document_analysis] every document page failed -> 04_Renal_Function_Electrolytes.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined
- Pipeline stage 'document4' (document_analysis) failed: [document_analysis] every document page failed -> 05_Coagulation_Profile.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined
- Pipeline stage 'document5' (document_analysis) failed: [document_analysis] every document page failed -> 06_Lipid_Profile.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined
- Pipeline stage 'document6' (document_analysis) failed: [document_analysis] every document page failed -> 07_CT_Brain_Without_Contrast.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined
- Pipeline stage 'document7' (document_analysis) failed: [document_analysis] every document page failed -> 08_MRI_Brain_DWI_ADC.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined
- Pipeline stage 'document8' (document_analysis) failed: [document_analysis] every document page failed -> 09_CT_Angiography_Head_Neck.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined
- Pipeline stage 'document9' (document_analysis) failed: [document_analysis] every document page failed -> 10_Carotid_Doppler.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined
- Pipeline stage 'document10' (document_analysis) failed: [document_analysis] every document page failed -> 11_ECG.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined
- Pipeline stage 'document11' (document_analysis) failed: [document_analysis] every document page failed -> 12_Echocardiogram.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined
- Pipeline stage 'document12' (document_analysis) failed: [document_analysis] every document page failed -> 13_Troponin_I.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined
- Pipeline stage 'consolidate' (cross_image_synthesis) skipped: upstream dependency failed
- Pipeline stage 'report' (report_synthesis) skipped: upstream dependency failed

## Run diagnostics

| Stage | Capability | Status | Time | Detail |
|---|---|---|---|---|
| `document0` | document_analysis | ❌ failed | 5.06s | [document_analysis] every document page failed -> 01_CBC (3).pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined |
| `document1` | document_analysis | ❌ failed | 6.61s | [document_analysis] every document page failed -> 02_Random_Blood_Glucose.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined |
| `document2` | document_analysis | ❌ failed | 4.81s | [document_analysis] every document page failed -> 03_HbA1c.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined |
| `document3` | document_analysis | ❌ failed | 4.81s | [document_analysis] every document page failed -> 04_Renal_Function_Electrolytes.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined |
| `document4` | document_analysis | ❌ failed | 6.49s | [document_analysis] every document page failed -> 05_Coagulation_Profile.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined |
| `document5` | document_analysis | ❌ failed | 4.93s | [document_analysis] every document page failed -> 06_Lipid_Profile.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined |
| `document6` | document_analysis | ❌ failed | 5.06s | [document_analysis] every document page failed -> 07_CT_Brain_Without_Contrast.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined |
| `document7` | document_analysis | ❌ failed | 4.80s | [document_analysis] every document page failed -> 08_MRI_Brain_DWI_ADC.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined |
| `document8` | document_analysis | ❌ failed | 4.07s | [document_analysis] every document page failed -> 09_CT_Angiography_Head_Neck.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined |
| `document9` | document_analysis | ❌ failed | 4.14s | [document_analysis] every document page failed -> 10_Carotid_Doppler.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined |
| `document10` | document_analysis | ❌ failed | 3.95s | [document_analysis] every document page failed -> 11_ECG.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined |
| `document11` | document_analysis | ❌ failed | 4.33s | [document_analysis] every document page failed -> 12_Echocardiogram.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined |
| `document12` | document_analysis | ❌ failed | 4.01s | [document_analysis] every document page failed -> 13_Troponin_I.pdf#page=1: NameError: name '_fallback_from_laboratory_text' is not defined |
| `consolidate` | cross_image_synthesis | ⏭️ skipped | - | upstream dependency failed |
| `report` | report_synthesis | ⏭️ skipped | - | upstream dependency failed |

---

_AI-generated draft for clinician review. This report is decision *support* only: it is not a diagnosis, has not been validated for clinical use, and must be verified by a qualified clinician (and a radiologist for imaging) before it informs patient care._ Report generated by Medical AOS at 2026-10-05 12:54:57 UTC.
