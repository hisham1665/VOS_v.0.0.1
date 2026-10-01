# Task Plan

**Job:** Interpret 2 image(s) and 2 document(s) (CT, Ultrasound) from study patient-002-acute-appendicitis-e43475bf and report patient status. History: abdominal pain Question: check it is ulcer or appendix

**Shape:** 10 nodes across 7 waves

| Node | Description | Capability | Depends on | Status | Time |
|---|---|---|---|---|---|
| `triage` | Identify the imaging modality and body region of every image, and flag any file that is not a medical image. | modality_triage | - | pending | - |
| `screen` | Route every image to the screener trained for its modality and region, and report candidate conditions with probabilities. | image_classification | `triage` | pending | - |
| `confirm` | Re-check each screening candidate, and each condition named in the referring question, with a disease-specific specialist model. | specialist_confirmation | `screen` | pending | - |
| `read0` | Produce a structured radiological read of ct.jpg, describing modality, image quality, observations with severity, and a short impression. | vision | `triage`, `screen`, `confirm` | pending | - |
| `read1` | Produce a structured radiological read of ultrasound.png, describing modality, image quality, observations with severity, and a short impression. | vision | `triage`, `screen`, `confirm` | pending | - |
| `document0` | Analyze document(s) blood_report.pdf, page(s) 1. Classify each document and extract clinically relevant observations and exact reported values, preserving source refs. | document_analysis | - | pending | - |
| `document1` | Analyze document(s) patient_info.txt, page(s) 1. Classify each document and extract clinically relevant observations and exact reported values, preserving source refs. | document_analysis | - | pending | - |
| `consolidate` | Merge the per-image and per-document reads into one coherent, de-duplicated findings list. Preserve exact document source references and reconcile disagreements without inventing facts. | cross_image_synthesis | `read0`, `read1`, `document0`, `document1`, `screen`, `confirm` | pending | - |
| `literature` | Look up the accepted differential, next investigation and red flags for the findings identified in this study. | web_search | `consolidate` | pending | - |
| `report` | Write the impression, overall patient status, triage level and recommended next steps for the referring clinician. | report_synthesis | `consolidate`, `literature` | pending | - |

## Execution waves

- **Wave 0** (3 in parallel): `triage`, `document0`, `document1`
- **Wave 1** (1 in parallel): `screen`
- **Wave 2** (1 in parallel): `confirm`
- **Wave 3** (2 in parallel): `read0`, `read1`
- **Wave 4** (1 in parallel): `consolidate`
- **Wave 5** (1 in parallel): `literature`
- **Wave 6** (1 in parallel): `report`

## Graph

```mermaid
flowchart TD
    subgraph W0["Wave 0"]
        triage["Identify the imaging modality and body regi...<br/>(modality_triage)"]
        document0["Analyze document(s) blood_report.pdf, page(...<br/>(document_analysis)"]
        document1["Analyze document(s) patient_info.txt, page(...<br/>(document_analysis)"]
    end
    subgraph W1["Wave 1"]
        screen["Route every image to the screener trained f...<br/>(image_classification)"]
    end
    subgraph W2["Wave 2"]
        confirm["Re-check each screening candidate, and each...<br/>(specialist_confirmation)"]
    end
    subgraph W3["Wave 3"]
        read0["Produce a structured radiological read of c...<br/>(vision)"]
        read1["Produce a structured radiological read of u...<br/>(vision)"]
    end
    subgraph W4["Wave 4"]
        consolidate["Merge the per-image and per-document reads ...<br/>(cross_image_synthesis)"]
    end
    subgraph W5["Wave 5"]
        literature["Look up the accepted differential, next inv...<br/>(web_search)"]
    end
    subgraph W6["Wave 6"]
        report["Write the impression, overall patient statu...<br/>(report_synthesis)"]
    end
    triage --> screen
    screen --> confirm
    triage --> read0
    screen --> read0
    confirm --> read0
    triage --> read1
    screen --> read1
    confirm --> read1
    read0 --> consolidate
    read1 --> consolidate
    document0 --> consolidate
    document1 --> consolidate
    screen --> consolidate
    confirm --> consolidate
    consolidate --> literature
    consolidate --> report
    literature --> report

    classDef cross_image_synthesisStyle fill:#00ACC1,color:#fff
    classDef image_classificationStyle fill:#E91E63,color:#fff
    classDef modality_triageStyle fill:#9C27B0,color:#fff
    classDef report_synthesisStyle fill:#F44336,color:#fff
    classDef visionStyle fill:#FF9800,color:#fff
    classDef web_searchStyle fill:#4CAF50,color:#fff
    class triage modality_triageStyle
    class screen image_classificationStyle
    class read0 visionStyle
    class read1 visionStyle
    class consolidate cross_image_synthesisStyle
    class literature web_searchStyle
    class report report_synthesisStyle
```
