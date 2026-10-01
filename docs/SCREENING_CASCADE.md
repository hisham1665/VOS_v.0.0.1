# Screening cascade: triage → screen → confirm → read

This document explains why the single pneumonia classifier was replaced, which
of the two proposed designs was implemented, what models now run on which
images, how well each one actually performed when tested, and — most
importantly — where the system still breaks.

> Everything here is research tooling. None of these checkpoints is a cleared
> medical device, and every number below comes from a small public benchmark
> (n ≈ 10–30 per class), not from a clinical validation.

---

## 1. What was wrong

Three separate bugs combined into "every image is read as a pneumonia X-ray":

| # | Bug | Effect |
|---|---|---|
| 1 | `screen` ran **in the same wave** as `triage` (both `depends_on=[]`) | Screening saw every non-DICOM image as modality `Unknown`. `Unknown` was in the classifier's allowed set, so CT, ultrasound and MRI all went through the chest model. |
| 2 | The only classifier was `lxyuan/vit-xray-pneumonia-classification` — **binary** NORMAL / PNEUMONIA | A binary model cannot say "fracture" or "haemorrhage". Anything abnormal-looking comes back as PNEUMONIA. |
| 3 | The VLM prompt was told *"a screening classifier scored this image: PNEUMONIA 53% — treat as a second opinion"* | The VLM was primed toward a chest/pneumonia read even on an abdominal CT. |

Plus two routing defects:

| # | Bug | Effect |
|---|---|---|
| 4 | Filename regex `\bct(?:[-_ ]|$)` required `-`, `_`, space or end after "ct" | `ct.jpg` (dot after) and `chest_ct.png` (`\b` treats `_` as a word char) were not recognised as CT. |
| 5 | General CLIP (`openai/clip-vit-base-patch32`) for modality | Measured **60%** modality accuracy; it labelled 47/48 brain MRIs as CT, and the synthetic demo chest film as "mammogram". |

And one silent gap: the consolidation step was instructed to compare VLM reads
against classifier output, but it never received the classifier output (it
depended only on the read nodes).

---

## 2. The two designs you proposed

### Option A — general abnormality detector → message → VLM concludes

```
image → general "is something abnormal, and roughly what?" model
      → that message goes to the VLM / LLM → conclusion
```

**Feasible:** yes. **Sufficient on its own:** no.

* There is no robust, open, *modality-agnostic* anomaly detector on Hugging
  Face. What exists are per-modality multi-label classifiers (e.g. NIH-14 for
  chest X-ray, 14 findings). So "general" really means "general *within one
  modality and region*" — you still need routing first.
* The conclusion step is a generative model. Whatever the screener says, the
  VLM can agree with fluently. One weak model + one persuadable model = the
  pneumonia bug again, just with more labels.

### Option B — general screener → candidate diseases → disease-specific models re-scan

```
image → general screener → candidates {infiltration 62%, effusion 41%}
      → for each candidate: run specialist(s) trained only on that disease
      → supported / not supported → VLM + consolidation conclude
```

**Feasible:** yes, *where specialists exist*. This is what was implemented,
with Option A's message-passing kept as the final step (the cascade's evidence
is handed to the VLM, scoped and caveated).

Why B is better than A:

* Two **differently trained** models agreeing is real evidence; one model's
  score is not.
* A specialist that disagrees (`not supported`) visibly lowers confidence
  instead of letting the VLM run with a false positive.
* The referring question becomes a prior: *"rule out pneumonia"* runs the
  pneumonia specialist even if the screener was quiet.

Where B honestly cannot work (and the system now says so instead of guessing):

* Most conditions have **no** usable specialist on the Hub (appendicitis,
  bowel obstruction, liver lesions, any ultrasound finding, knee MRI, spine…).
  Those candidates are reported `unverifiable`.
* When the screener is the *only* model that covers a condition (e.g. fracture),
  re-running it is not a second opinion. The cascade refuses to do that and
  marks it `unverifiable`.
* Specialists are only as good as their training data — several popular ones
  failed the benchmark outright (§4).

---

## 3. What runs now

```
              ┌──────────────┐
  images ───▶ │   triage     │  general-CLIP gate: medical or not?
              │              │  PubMedCLIP: modality + body region
              └──────┬───────┘  DICOM header authoritative; filename = prior
                     ▼
              ┌──────────────┐
              │   screen     │  stage 1: model_zoo.screeners_for(modality, region)
              │              │  → candidate conditions (recall-oriented)
              └──────┬───────┘  unroutable image → "NOT SCREENED (reason)"
                     ▼
              ┌──────────────┐
              │   confirm    │  stage 2: each candidate (+ conditions in the
              │              │  referring question) → independent specialist
              └──────┬───────┘  → supported / not_supported / equivocal / unverifiable
                     ▼
       read0 … readN (VLM, with scoped cascade evidence)   documentN
                     ▼
              consolidate  ◀── also receives screen + confirm directly
                     ▼              (but refuses to run on cascade output alone)
             literature → report (with a per-image "Screening cascade" table)
```

Files:

| File | Role |
|---|---|
| `capabilities/model_zoo.py` | The only place model ids live: modality, region, role, label → condition mapping, thresholds, benchmark notes. JSON-overridable via `MAOS_MODEL_ZOO`. |
| `capabilities/modality_triage.py` | Two-CLIP triage, prompt ensembling, header/filename/CLIP arbitration. |
| `capabilities/image_classification.py` | Stage 1 routing + screening; shared inference helpers. |
| `capabilities/specialist_confirmation.py` | Stage 2 re-check and verdicts. |
| `agents/planner.py` | `triage → screen → confirm → read`; consolidation depends on the cascade too. |
| `capabilities/hf_backend.py` | LRU eviction (`MAOS_MAX_RESIDENT_MODELS`, default 4) so a dozen checkpoints fit a 4 GB GPU. |

### Model roster (`capabilities/model_zoo.py`)

| Key | Model | Role | Modality / region | Checks | Status |
|---|---|---|---|---|---|
| — | `openai/clip-vit-base-patch32` | gate | any | medical vs not | ✅ |
| — | `flaviagiammarino/pubmed-clip-vit-base-patch32` | triage | any | modality + region | ✅ |
| `cxr_nih14` | `Sohaibsoussi/ViT-NIH-Chest-X-ray-dataset-small` | screener | X-ray / chest | 14 NIH findings (multi-label) | ✅ |
| `cxr_pneumonia` | `lxyuan/vit-xray-pneumonia-classification` | specialist | X-ray / chest | pneumonia | ✅ (non-specific, see §4) |
| `cxr_tuberculosis` | `runaksh/chest_xray_tuberculosis_detection` | specialist | X-ray / chest | tuberculosis | ✅ (loaded with base ViT processor) |
| `cxr_xrv_all` | TorchXRayVision `densenet121-res224-all` | specialist | X-ray / chest | pneumonia, pneumothorax (refute only), effusion, rib fracture, oedema, cardiomegaly, mass, nodule, atelectasis | ✅ (`capabilities/xrv_backend.py`) |
| `cxr_pneumothorax` | `mrm8488/vit-base-patch16-224_finetuned-pneumothorax` | specialist | X-ray / chest | pneumothorax | ❌ **disabled** — failed benchmark |
| `msk_fracture` | `Hemgg/bone-fracture-detection-using-xray` | screener | X-ray / limb, pelvis, spine | fracture | ❌ **disabled** — failed benchmark |
| `ct_head_hemorrhage` | `DifeiT/rsna-intracranial-hemorrhage-detection` | screener | CT / head | intracranial haemorrhage | ✅ (behaves as "abnormal head CT") |
| `ct_head_stroke` | `BTX24/hiera-base-224-in1k-hf-finetuned-stroke-binary` | specialist | CT / head | stroke | ✅ (cross-checks haemorrhage) |
| `ct_chest_lung_cancer` | `oohtmeel/swin-tiny-patch4-finetuned-lung-cancer-ct-scans` | screener | CT / chest | lung cancer | ✅ |
| `ct_kidney_tumor` | `Rahii123/kidney-disease-classification-v2` | screener | CT / abdomen | kidney tumour | ❌ **disabled** — failed benchmark |
| `mri_brain_tumor` | `Devarshi/Brain_Tumor_Classification` | screener | MRI / head | glioma, meningioma, pituitary | ✅ |
| `mri_brain_tumor_vit` | `Hemgg/brain-tumor-classification` | specialist | MRI / head | brain tumour (2nd opinion) | ✅ |
| `mri_brain_dementia` | `evanrsl/resnet-Alzheimer` | specialist | MRI / head | dementia (only if asked) | ✅ |

Disabled entries stay in the table on purpose: the gap shows in `--doctor`,
and a better checkpoint can be switched in with a `MAOS_MODEL_ZOO` JSON file
without touching code.

Fan-out rules (`RECHECK`): a specific candidate goes to its own specialist(s)
(infiltration/consolidation → pneumonia + TB; nodule/mass → TB + lung cancer;
haemorrhage → stroke). A film whose labels are each below threshold but whose
overall abnormality crosses the screener's `abnormal_threshold` gets an
`abnormal` candidate and is sent to **every** applicable specialist — this is
what caught the pneumonia film in §4.4 that the screener only scored 16%.

### Coverage map — what actually gets a supervised check

| | Chest | Head | Abdomen | Limb / MSK | Spine | Breast |
|---|---|---|---|---|---|---|
| **X-ray** | ✅ screener + 3 specialists | — | ❌ | ❌ (no model passed) | ❌ | — |
| **CT** | ✅ lung cancer only | ✅ bleed + stroke | ❌ (kidney models failed) | ❌ | ❌ | — |
| **MRI** | ❌ | ✅ tumour ×2, dementia | ❌ | ❌ | ❌ | ❌ |
| **Ultrasound** | ❌ | — | ❌ | ❌ | — | ❌ |
| **Mammo / PET / pathology** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

Every ❌ means: triage still identifies the modality, the VLM still reads it,
but no supervised model corroborates — and the report says so.

---

## 4. Benchmark

Small labelled sample pulled from public Hugging Face datasets (test splits
where they exist). **n is tiny and several datasets overlap with what the
models were trained on**, so treat good numbers as an upper bound and bad
numbers as decisive.

### 4.1 Modality / region triage (241 images)

| Approach | Modality accuracy | Notes |
|---|---:|---|
| Old: general CLIP, 1 prompt/class | **60%** | Brain MRI → CT 47/48; breast US 5/12 |
| General CLIP, richer prompts | 76% | MRI still → CT |
| PubMedCLIP, 1 prompt/class | 92% | Calls photos "MRI"/"pathology" |
| PubMedCLIP, richer prompts | 95% | |
| **New: general-CLIP gate + PubMedCLIP, richer prompts** | **96%** | Photos 10/11 rejected |

Region (chest / head / abdomen / limb / …) with PubMedCLIP: **95%**.
Weakest class everywhere: **breast ultrasound** (~50% modality, 33% region).

### 4.2 Disease classifiers

"Abnormal" = 1 − P(normal). AUC = abnormal vs normal on the first dataset
listed. Counts are "called abnormal at > 0.5".

| Model | Tested on | AUC | Key behaviour | Decision |
|---|---|---:|---|---|
| NIH-14 ViT (`cxr_nih14`) | Kermany test (12 N / 12 P) | **0.97** | 11/12 pneumonia, 2/12 normal flagged; SIIM: 5/10 non-PTX flagged | **Stage-1 screener** |
| CheXpert-5 ConvNeXt | same | 0.62 | Everything ≈ 0.3; no separation | Rejected |
| `lxyuan` pneumonia (old default) | same | **1.00** | but 7/10 *normal adult* SIIM films → "pneumonia" | Specialist only |
| `nickmuchi` pneumonia | same | 1.00 | same Kermany bias (5/10 SIIM) | Not needed |
| `runaksh` TB | Kermany + COVID set | n/a (no TB positives obtained) | 0/12 pneumonia, 2/12 normal → TB; **5/6 COVID → TB** | Specialist, non-specific |
| **TorchXRayVision** `densenet121-res224-all` | Kermany, COVID set, SIIM | pneumonia **0.97**, PTX 0.71 | Pneumonia via Consolidation 18/18 vs normals 2/18. PTX: true PTX 0.50–0.51, but 4/10 PTX-negative and **all** pneumonia films 0.50–0.54 → not separable. Fracture: 3/12 limb (rib/clavicle label only) | **Chest specialist**; PTX refute-only |
| `mrm8488` pneumothorax | SIIM (10 / 10) | **0.38** | 12/12 *normal* films → pneumothorax | **Disabled** |
| `Hemgg` fracture | FracAtlas fractures + chest films | 0.72 | **24/24 chest X-rays "fractured" at 99 %** | **Disabled** |
| `prithivMLmods` fracture (SigLIP, 1.9 GB) | same | 0.37 | 12/12 normal chest films "fractured" | Rejected |
| MURA abnormality ViT (1.4 GB) | same | 0.15 | inverted / random | Rejected |
| `Devarshi` brain tumour (Swin) | Kaggle MRI test (16 tumour / 8 none) | **1.00** | 14/16 tumours, 0/8 normals; ~50 % "tumour" on head **CT** | **MRI screener** |
| `Hemgg` brain tumour (ViT) | same | 0.99 | 15/16, 0/8 | MRI specialist |
| `evanrsl` Alzheimer | OASIS slices | 1.00\* | 3/8 tumour-free MRIs → "demented" | Only when asked |
| `DifeiT` haemorrhage | TEKNOFEST stroke CT | 0.88 | **6/6 brain-tumour CTs → "haemorrhage" 100 %** | Head-CT screener |
| `BTX24` stroke | TEKNOFEST stroke CT | 1.00\* | 6/6 tumour CTs → "stroke" | Head-CT specialist |
| `oohtmeel` lung cancer | Chest-CT test (24 ca / 8 normal) | **0.96** | 0/8 false positives, only 9/24 cancers > 0.5 | Chest-CT screener (thr 0.3) |
| `Rahii123` kidney tumour | chest/head CT (out of region) | — | **8/8 normal chest CTs → "Tumor"** | **Disabled** |
| `Haaaaaaaaaax` Bosniak | same | — | 32/32 chest CTs → cyst | Rejected |

\* Benchmarked on (part of) its own training dataset — the number is meaningless.

### 4.3 Cascade vs. the old single model (62 chest X-rays)

With TorchXRayVision (xrv) added as a second, independently trained
pneumonia check:

| Set | Old: `lxyuan` alone → "pneumonia" | New: pneumonia supported by ≥ 1 model | New: supported by **both** (`lxyuan` + xrv) |
|---|---:|---:|---:|
| Normal (Kermany + COVID set) | 1 / 18 | **0 / 18** | 0 / 18 |
| Pneumonia | 18 / 18 | 16 / 18 | 16 / 18 |
| COVID-19 | 4 / 6 | 5 / 6 | 3 / 6 |
| SIIM adult, no pneumothorax | 7 / 10 | 5 / 10 | **3 / 10** |
| SIIM adult, pneumothorax | 9 / 10 | 6 / 10 | 4 / 10 |

Pneumothorax with xrv: never "supported" (by design), "equivocal" on 2/10
true PTX and 1/10 PTX-negative, "not supported" on 1/10 PTX-negative.

Reading:
* **Two-model agreement is the useful signal.** Where both models agree, false
  pneumonia on adult films falls from 7/10 (old) to 3/10; true pneumonia
  keeps 16/18. The report lists each specialist's verdict separately, so
  consolidation and the reader see agreement vs. a lone vote.
* The big wins remain routing: CT, MRI, ultrasound and photos never touch a
  chest model (§4.4).
* Pneumothorax is still **not solved**. xrv can say "not supported" when its
  score is low, and flags "equivocal" otherwise, prompting a human look — it
  cannot confirm one. No checkpoint tested could.

### 4.4 End-to-end routing on an anonymised mixed study

Ten files named `IMG_001.jpg … IMG_010.jpg` (no filename hints), real models:

| Truth | Triage | Stage 1 | Stage 2 |
|---|---|---|---|
| Pneumonia CXR | X-ray / chest 87 % | NIH-14: abnormal 19 % | **pneumonia supported by both** (`lxyuan` 99 %, xrv 59 %); TB not supported 7 %; PTX equivocal; rib fracture not supported |
| Normal CXR | X-ray / chest 88 % | nothing (0 %) | — |
| Stroke head CT | CT / head 99 % | haemorrhage 100 % | stroke supported 100 % |
| Tumour head CT | CT / head 93 % | haemorrhage 100 % ⚠️ | stroke supported ⚠️ |
| Meningioma MRI | MRI / head 99 % | brain tumour 100 % | **2nd model supports, 99 %** |
| "No-tumour MRI" (Kaggle label) | **CT** / head 99 % | haemorrhage 100 % | stroke supported |
| Lung adenocarcinoma CT | CT / chest 89 % | lung cancer 40 % | unverifiable (single model) |
| Wrist fracture X-ray | X-ray / limb 99 % | **not screened** | — |
| Malignant breast US | Ultrasound / breast 77 % | **not screened** | — |
| Holiday photo | **rejected** (non-medical 97 %) | not screened | — |

The "no-tumour MRI" row: the image is really a **head CT with a large
subdural haematoma and midline shift**, mislabelled in the public Kaggle MRI
set. Triage and the haemorrhage model were right; the dataset was wrong.
Public medical datasets are noisy — and so is every number here.

Under the old pipeline all ten files would have gone through the binary
pneumonia model.

---

## 5. Limitations — where it fails

### 5.1 Fundamental limits (design-level)

1. **2D, single slice.** Every model sees one image or one DICOM frame. CT
   and MRI are volumes; a 1 cm lesion lives on 2–3 of 300 slices. No 3D
   context, no comparison with priors, no measurement.
2. **Closed label sets.** "No candidate" means "nothing *this model knows*".
   The report says so, but a reader who skips the caveat will over-trust a
   quiet screener.
3. **Correlated training data.** Most Hub checkpoints are fine-tuned on the
   same few Kaggle sets. Two models agreeing is weaker evidence when they
   learned from the same images (`mri_brain_tumor` + `mri_brain_tumor_vit`).
4. **Domain shift.** Kermany is paediatric (age 1–5); its pneumonia model
   calls ~half of normal adult films pneumonia. Expect the same with other
   scanners, portable AP films, phone photos of monitors, burned-in
   annotations, non-standard CT windows.
5. **The VLM still concludes.** The cascade makes the prompt specific and
   gives consolidation something checkable. A VLM can still hallucinate; the
   cascade makes disagreement visible, it does not prevent it.

### 5.2 Use cases where it will break

| Scenario | What happens | Why |
|---|---|---|
| **Pneumothorax** (CXR) | Never confirmed; at best "equivocal" from xrv | Dedicated PTX model failed (disabled); xrv PTX scores overlap pneumonia films. Time-critical finding → VLM + radiologist are the real defence. |
| **Fracture** (limb, spine, pelvis) | Not screened; VLM only | All three fracture models failed. xrv's fracture label covers ribs/clavicle on chest films only (3/12 limb fractures). |
| **Rib fracture on CXR with chest drain / trauma** | Often "equivocal" | xrv fracture scores 0.50–0.51 on SIIM films with drains — confounded. |
| **Abdominal CT** (appendicitis, obstruction, perforation, liver, kidney) | Not screened; VLM only | No usable model. Your appendicitis study is here. |
| **Any ultrasound** | Triaged (~50 % for breast US), never screened | No US model; region often wrong ("neck" for the abdominal US). |
| **Head CT with tumour / abscess / oedema** | "Haemorrhage 100 %, stroke supported" | Both head-CT models are really "abnormal head CT" detectors: right that something is wrong, wrong about what. |
| **Head CT bone window / CTA / contrast** | Unreliable | Trained on non-contrast brain-window slices. |
| **Brain MRI: stroke, MS, bleed, post-op** | "No tumour", or forced into a tumour class | Tumour models know 3 tumours + none. |
| **Brain MRI sagittal/coronal or non-T1c** | Unreliable | Kaggle set is mostly axial T1-contrast. |
| **Adult chest films** | Pneumonia over-called | Paediatric training data. |
| **COVID / viral pneumonia** | Often "TB supported" | TB model non-specific for bilateral opacity (5/6). |
| **Lateral, portable AP, rotated CXR** | Lower accuracy | Training sets mostly PA. |
| **Lung nodule follow-up on CT** | Binary score, often < 0.5 for real cancer | No size, no Lung-RADS, 9/24 sensitivity at 0.5. |
| **Mammography, PET, pathology** | Triaged, never screened | No model in the zoo. |
| **Screenshots / phone photos of a screen** | May pass the gate and be routed | Gate tuned on everyday photos vs. scans; a photo of a CXR is in between. |
| **Synthetic images** (demo `chest_xray_*.png` stripes) | Triaged "Unknown", not screened | Correct — but demo data won't exercise the cascade. |
| **DICOM with wrong Modality tag** | Trusted absolutely | Header is authoritative by design. |
| **`MAOS_BACKEND=remote`** | "Screener failed to run" | Community checkpoints are not on HF serverless inference; cascade needs local `torch` + `transformers`. |
| **`torchvision` mismatched with `torch`** | xrv import fails → `--doctor` problem; specialist skipped | `pip install torchxrayvision` alone can pull a CPU torch. Install matching `torchvision` from the PyTorch index first (see `requirements.txt`). |
| **CPU-only / small GPU** | Slower; first run downloads ~3 GB | LRU keeps ≤ 4 classifiers resident (`MAOS_MAX_RESIDENT_MODELS`). |

### 5.3 Advantages over the old design

* **No cross-modality contamination** — CT/MRI/US/photos never reach a
  chest-X-ray model (`test_ct_slice_never_reaches_a_chest_xray_model`).
* **Honest silence** — uncovered images say *not screened (reason)*.
* **Independent corroboration** where it exists, explicit `unverifiable`
  where it doesn't; a screener is never its own second opinion.
* **Question-driven checks** — "rule out pneumonia" runs the pneumonia
  specialist even when the screener is quiet.
* **Triage 60 % → 96 %**, photos rejected, filename-hint bugs fixed.
* **Auditable** — one file holds every model, threshold and benchmark note;
  the report shows per-image screener output and specialist verdicts.
* **Extensible without code** — `MAOS_MODEL_ZOO=zoo.json` adds, replaces or
  disables entries.

### 5.4 What would move the needle next

1. ~~TorchXRayVision~~ — **done** (`cxr_xrv_all`, `capabilities/xrv_backend.py`).
   Next on chest X-ray: a pneumothorax model that actually separates PTX from
   consolidation (e.g. a SIIM-ACR segmentation winner), which needs a
   segmentation adapter rather than a classifier.
2. **TotalSegmentator / MONAI bundles** for CT: 3D, validated, whole-series
   models instead of 2D slice classifiers. `ingest` already decodes series.
3. **BiomedCLIP** (`open_clip`) as a zero-shot screener for regions with no
   supervised model (abdomen, ultrasound) — weaker than supervised, better
   than nothing.
4. **A local validation set** from the deployment site. Every number here is
   from public data.
5. **Run the VLM locally** (Ollama `medgemma`). HF serverless currently
   rejects both `google/medgemma-4b-it` and `Qwen/Qwen2.5-VL-7B-Instruct`
   for this token (`model_not_supported`), so image reads fail without Ollama.

### Reproducing the benchmark

The scripts used are not part of the package (they download public datasets).
The procedure: pull 6–12 images per class from the HF datasets named in §4
via the datasets-server `/rows` API, run each model with
`pipeline("image-classification", top_k=None)`, and compute abnormal-vs-normal
AUC. Triage was scored against the dataset's modality.
