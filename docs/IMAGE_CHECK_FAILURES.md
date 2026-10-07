# Image read failures: diagnosis and fix

The latest saved image run decoded all three supplied raster images successfully
(one JPG, two PNGs), with zero skipped inputs. The failure occurred in the shared
vision backend, after technical quality checks and modality triage.

## Root cause

The previous default changed to `gemma3:4b`, which was not installed. Ollama had
`medgemma1.5:latest`, which earlier runs successfully used. Auto-routing rejected
the missing default and switched to Hugging Face. Both configured hosted models
(`google/medgemma-4b-it` and `Qwen/Qwen2.5-VL-7B-Instruct`) returned
`model_not_supported` for the enabled providers. Consequently every direct-image
read, and visual document verification, failed. This was independent of the
source image extension.

Separately, the supervised catalog has no screener for the observed abdomen CT
and neck ultrasound routes. “Not screened” means no supervised disease-model
check; it does not mean that image decoding failed or a VLM read was impossible.

## Changes

- Auto mode first checks the existing `medgemma1.5:latest` model, then the documented
  `gemma3:4b` baseline if installed. Every selected model must advertise `vision`
  through Ollama `/api/show`. An explicit `MAOS_OLLAMA_MODEL` is never overridden.
- The selected alias is used in the actual request, cache identity, provenance
  and `--doctor` output. Diagnostics list missing/text-only models and distinguish
  an unavailable server from an unavailable model.
- Chat summaries show completed/failed direct-image reads separately from
  supervised screening coverage. The image table labels supplied images as
  `completed` or `unavailable`, instead of implying all decoded files were read.
- Permanent unsupported-model HTTP errors are not retried merely because their
  request identifier contains digits resembling a transient HTTP status.

All supported raster formats share this backend: `.jpg`, `.jpeg`, `.png`, `.bmp`,
`.tif`, `.tiff`, `.webp` (case-insensitive). They are decoded to prepared RGB JPEGs
before model inference. DICOM `.dcm`, `.dicom`, `.ima` and extensionless DICOM use
the frame-aware decoder, then the same vision backend. This list does not imply
support for every possible image/video format or acquisition protocol.

## Verified on the installed Windows Ollama setup

Ollama was initially stopped during investigation; its existing server was
started without downloading/replacing models. `/api/show` verified that
`medgemma1.5:latest` is Gemma 3 family, 4.3B, Q4_K_M, with `completion` and `vision`
capabilities. The application's auto route selected this alias locally.

All three originally failing image calls completed without backend errors:
`ct.jpg` (~25s), `ultrasound.png` (~9s), and the newly uploaded PNG figure (~12s).
These checks used temporary caches and
did not regenerate archived reports or alter stored patient assessments. Successful
invocation is not a clinical accuracy evaluation.

Offline verification: **281 tests passed in 8.97s** (77 current cases plus 204
archived regressions, with the previously documented two temporary fixture
updates). Coverage includes every supported raster extension, DICOM, installed
model selection, explicit configuration, text-only rejection, cache/request
identity, summary wording and permanent-error retry behavior. Compilation and
whitespace checks for the changed tracked implementation files passed.

## Running again

Start Ollama on Windows, then restart Medical AOS so the updated routing loads:

```powershell
ollama serve
```

If the Ollama tray application is already serving, use that running server.
To explicitly pin the verified existing model, use:

```dotenv
MAOS_VLM_BACKEND=ollama
MAOS_OLLAMA_MODEL=medgemma1.5:latest
```

Reopen the patient session and run `/analyze` (optionally with the referring
question). Old reports retain their original failed-run diagnostics; only a new
analysis uses the corrected path. Use `python main.py --doctor` to inspect the
selected model. The checked Windows localhost server is not automatically
reachable from this WSL environment; run the app in the same Windows environment
as before, or explicitly configure a reachable server URL for WSL.
