# Failure-point audit

Every failure mode found in the v0.0.2 codebase, what it did, and how it is
handled now. Ordered by severity.

---

## 1. The package could not be imported at all

**Severity: fatal — the system did not run.**

Modules disagreed about their own package name:

```python
# main.py, sub_agent.py, manager_agent.py
from aos_v0.agents.manager_agent import ManagerAgent   # package is medical_aos

# integrator_agent.py, graph_utils.py, diagram_utils.py
from graph_utils import get_sink_nodes                 # bare, top-level import
```

Both spellings existed in the same call graph, so no `sys.path` value could
satisfy both. `main.py` also did `sys.path.insert(0, parent.parent)`, reaching
outside the project. `test_graph_executor.py` hard-coded
`C:/Users/Asus/Desktop/project/tiny/aos_v0` — a different machine's directory.

**Now:** one consistent flat import root, `main.py` inserts its own directory,
and no absolute paths appear anywhere. Verified by `tests/` importing every
module.

---

## 2. Any single capability error killed the whole run

**Severity: high — total loss of work.**

`SubAgent.perform` called `fn(node.input, ...)` with no error handling, and
`GraphExecutor.run` called `future.result()` bare. One HTTP 503 from one image
aborted the entire study, discarding every other image's completed analysis.
The `"failed"` status existed in the model and was never assigned.

**Now:** `SubAgent.perform` always returns with the node in a terminal state.
A failed node marks only its transitive descendants `skipped`
(`graph_utils.descendants_of`); independent branches finish. The report is
still produced and flagged `degraded`.

Covered by the current offline execution and continuity tests in
`tests/test_aos_continuity.py` and `tests/test_adaptive_imaging.py`.

---

## 3. Unbounded thread creation, and a crash on empty waves

**Severity: high.**

```python
with ThreadPoolExecutor(max_workers=len(wave)) as pool:
```

A pool was constructed *per wave*, sized to the wave. A 40-image study created
40 threads, each independently loading model weights. And `max_workers=0`
raises `ValueError` — so an empty wave crashed the executor outright.

**Now:** one pool for the whole run, bounded by `MAOS_MAX_WORKERS`, plus a
separate `MAOS_MAX_GPU_WORKERS` semaphore so a 4GB card never runs two vision
models at once. Empty waves are skipped with a log line.

The current dependency-ready scheduling check is
`tests/test_aos_continuity.py::test_ready_scheduler_starts_child_before_unrelated_slow_root_finishes`.

---

## 4. Data race on shared vision output

**Severity: high — silent wrong output.**

```python
vision_outputs: dict[str, str] = {}      # written from worker threads
...
if node.capability == "vision":
    vision_outputs[node.id] = node.output
```

Worker threads mutated this dict while other threads in the same wave read it
to build their prompts. Whether a node saw the vision context depended on
thread scheduling — the same study could produce different reports on
different runs.

**Now:** no shared mutable state is written from workers. Node inputs are
assembled on the orchestrating thread *before* the wave starts, and results are
merged after it ends. Data reaches children through typed `artifacts` on the
graph edges.

Artifact propagation is exercised by the source-linked consolidation and report
tests in `tests/test_aos_continuity.py`.

---

## 5. `len(node.output)` on a `None` output

**Severity: medium — crash during logging.**

```python
print(f"... complete ({len(node.output)} chars)")
```

`Node.output` is `Optional[str]`. Any capability returning `None` — which the
`or ""` fallbacks made possible — raised `TypeError` inside the log statement,
in the executor, taking down the run during what should be a no-op.

**Now:** empty output is an explicit node failure with a clear message, checked
before any formatting. The result-contract checks run as part of the current
offline suite.

---

## 6. `KeyError` on a hallucinated capability name

**Severity: medium.**

```python
fn = CAPABILITY_MAP[self.capability]
```

The planner was free-text prompted with a capability list and nothing validated
its output. A typo (`"websearch"`) surfaced as a bare `KeyError` deep in a
worker thread.

**Now:** the registry raises `UnknownCapabilityError` listing the valid names,
the planner's graph is validated against the *live* registry before execution,
and an invalid name fails one node rather than the run.

---

## 7. Generated content overwrote hand-written documentation

**Severity: medium — data loss.**

`ManagerAgent._ensure_readme()` rewrote `README.md` on **every run**, splicing
in a hard-coded string constant and truncating whatever was there from the
marker onwards. Any edit a human made to the README was destroyed by the next
run.

**Now:** removed. `ManagerAgent.write_plan()` writes only to an explicit path
(`outputs/plan.md`). Nothing auto-writes `README.md`.

---

## 8. Two extra LLM round trips on the critical path

**Severity: medium — latency and cost.**

Every run spent one call generating the DAG and another auditing it for
"completeness", on top of a retry loop that regenerated the graph from scratch.
For a radiology read the DAG never varies in shape.

**Now:** `agents/planner.py` builds the plan in code — zero calls, and
auditable by inspection. The LLM planner survives for genuinely open-ended
jobs (`--ask`), with the completeness audit off by default
(`MAOS_COMPLETENESS_CHECK=1` restores it).

---

## 9. Multi-megabyte images sent verbatim to the model

**Severity: medium — latency and cost.**

`capabilities/vision.py` base64-encoded the source file with no resizing. The
repository's own `image.jpg` is **6.7MB** (5000×3333), which becomes ~9MB of
base64 per request — for a model that downsamples to ~896px internally anyway.

**Now:** every image is decoded, windowed, resized to `MAOS_MAX_IMAGE_EDGE` and
re-encoded once, then cached by content hash.

Measured on that file: **6722KB → 279KB, a 24× reduction**; the second call is
a cache hit at 0.011s versus 0.48s.

---

## 10. No timeouts anywhere

**Severity: medium — indefinite hang.**

No client had a timeout configured. A stalled endpoint hung the run forever,
with no output and no way to know which node was stuck.

**Now:** `MAOS_CALL_TIMEOUT` (default 180s) is enforced per capability call and
passed to every client. Exceeding it fails that node only.
`tests/test_adaptive_imaging.py::test_global_deadline_stops_downstream_and_retries`.

---

## 11. No retries for transient errors

**Severity: medium.**

A single 429, a cold-start "model is loading", or a dropped socket failed the
run permanently.

**Now:** `core/retry.py` retries with exponential backoff and jitter, but *only*
for errors classified transient — a malformed argument still fails immediately
rather than being retried three times.

---

## 12. Missing credentials surfaced as opaque SDK errors

**Severity: medium — bad diagnosis.**

`Groq(api_key="")` was constructed at *module import time*, so a missing key
failed during import with an SDK-internal message. There is no `.env` in this
repository, so this was the first thing a new user would hit.

**Now:** clients are lazy and raise `ConfigError` naming the variable and where
to get it. `python main.py --doctor` reports every credential, model and
dependency before a study is ever loaded.

---

## 13. Single image only — the actual requirement was unimplemented

**Severity: fatal for the use case.**

The system accepted `--image <one file>`. A doctor's folder of X-rays and scans
had no path through the code.

**Now:** `ingest/study_loader.py` walks a folder recursively, decodes DICOM and
standard formats, absorbs `.json`/`.txt` sidecars as patient context,
de-duplicates by content hash, samples very large series, and records every
skipped file instead of failing.

---

## 14. No DICOM support

**Severity: fatal for the use case.**

Real scans are `.dcm` with 12–16-bit pixel data. `PIL.Image.open` cannot read
them, and even raw pixels would render as a near-black rectangle without
rescale and windowing.

**Now:** `ingest/dicom_utils.py` applies the modality LUT
(`RescaleSlope`/`Intercept`), the window preset, `MONOCHROME1` inversion, and
all-frame ingestion for multi-frame objects (the compatibility preview API still
selects one frame). Missing `pydicom` degrades to
"DICOM skipped" rather than crashing.

---

## 15. Patient identifiers were extracted and forwarded unfiltered

**Severity: high — privacy.**

No de-identification existed, and nothing stopped patient data from being
placed into an outbound web search query.

**Now:** DICOM PHI tags are stripped at ingest (`MAOS_DEIDENTIFY`, on by
default), and `web_search._build_query` constructs its query from finding
labels only — enforced in code, not by prompt instruction.

The current DICOM, source-binding and literature-privacy behaviors are covered
by the adaptive-imaging suite and the capability tests collected by
`python -m pytest tests/ -q`.

---

## 16. Clinical severity could be silently downgraded

**Severity: high — patient safety.**

The final answer was whatever text the last node emitted. Nothing constrained
it, so a model could describe a pneumothorax and still summarise the study as
unremarkable.

**Now:** overall severity is computed from the structured findings, never
parsed from prose, and the triage level is floored by the worst finding at two
independent layers (`report_synthesis` and `IntegratorAgent`). A model claiming
"routine" over a critical finding is overridden and the override is logged.

Covered by
`tests/test_aos_continuity.py::test_synthesis_failure_is_degraded_and_integrator_enforces_triage_floor`.

---

## 17. No output validation on model JSON

**Severity: medium.**

Model responses were used directly. A fenced code block, a preamble, or an
invalid enum value would crash or silently corrupt the result.

**Now:** `_parse_json` tolerates fences and surrounding prose; `_normalise_findings`
drops malformed entries, coerces invalid severities to `indeterminate`, and
clamps confidence to `[0,1]`.
`tests/test_adaptive_imaging.py::test_report_payload_normalizes_string_evidence_without_accepting_unlinked_claims`.

---

## 18. Mermaid diagrams could be emitted invalid

**Severity: low.**

Node ids went into the diagram unsanitised, so an id containing `-` or a
description containing `[` produced a diagram that would not render.

**Now:** ids and labels are both sanitised, and failed/skipped nodes are
outlined so a degraded run is visible in the diagram.

---

## 19. Repository hygiene

**Severity: low.**

`.gitignore` contained only `/.env`, so `__pycache__` and 16 `.pyc` files were
tracked, and a 6.7MB `image.jpg` sat in the repo root.

**Now:** a complete `.gitignore` that also excludes `studies/`, `*.dcm` and
`phi/` — patient data must never reach version control. The tracked `.pyc`
files have been removed from the index.

---

# Bugs found by running it, after the rewrite

The three below all shipped with a green test suite and were caught only by a
real end-to-end CLI run. They are represented by the current regression suites
under `tests/`.

## 20. Registry race: "Unknown capability ... Registered: []"

**Severity: high — nondeterministic node failure.**

```python
def _ensure_loaded():
    global _loaded
    if _loaded:
        return
    _loaded = True          # set BEFORE the imports below have run
    from capabilities import image_classification, ...
```

Classic broken double-checked locking. The executor calls `registry.get()` from
several worker threads simultaneously. Thread A set `_loaded = True` and began
importing; thread B saw the flag, skipped the import, and looked up a name in a
still-empty dict.

On the live run this failed the `screen` node in wave 0 and skipped all five
downstream nodes — the whole study lost, from a pure timing accident. It never
reproduced single-threaded, which is why no unit test caught it.

**Now:** the import runs under a lock and `_loaded` is set only once every
module has registered itself.

Registry loading and capability discovery are exercised by the current
capability-registry tests in `tests/test_aos_continuity.py` and the full suite.

## 21. A failed run reported the patient as NORMAL

**Severity: critical — patient safety.**

`max_severity([])` returns `NORMAL` by definition. When every analysis stage
failed, the findings list was empty, so the integrator computed the overall
status as:

```
report assembled: 0 finding(s), status=normal, degraded=True
```

A study that was never successfully read presented an all-clear. The `degraded`
flag was set, but the headline status — the line a busy clinician actually reads
— said normal.

**Now:** an empty findings list yields `INDETERMINATE`, never `NORMAL`, at both
the capability and integrator layers. A genuinely normal read (one finding, with
severity `normal`) still correctly reports `NORMAL`.

The current invariant is exercised by
`tests/test_adaptive_imaging.py::test_unread_direct_image_prevents_normal_status_from_document_findings`.

## 22. UnicodeEncodeError killed the run at the final print

**Severity: medium — total output loss on Windows.**

```
UnicodeEncodeError: 'charmap' codec can't encode character '🟢'
```

The severity badges (🔴🟠🟢) cannot be encoded in cp1252, the default Windows
console code page. The pipeline completed, wrote both report files, and then
died rendering the terminal summary — exiting `0` with no visible output, which
is the worst possible combination: it looks like success and shows nothing.

**Now:** `main._force_utf8_output()` reconfigures stdout/stderr to UTF-8 with
`errors="replace"` before any output, and tolerates streams that refuse to be
reconfigured.

The UTF-8 output path remains implemented in `main._force_utf8_output()` and is
included in the CLI/reporting implementation review.

## 23. Every parallel read paid the Ollama probe timeout separately

**Severity: low — latency.**

`_ollama_reachable()` memoised its result, but the memo was written *after* the
1.5s connection timeout. With three reads running concurrently, all three
probed before any of them cached the answer — visible as a flat 3.16s on every
read node in the live run's diagnostics table.

**Now:** the probe is locked, so exactly one connection attempt happens per
process.

---

## Remaining limitations

These are **not** fixed, by design, and matter before any clinical use:

1. **No model is clinically validated.** The default classifier is a
   pneumonia-screening checkpoint; the VLM is a general medical model. Neither
   is a regulated device. See `docs/SAFETY.md`.
2. **Multi-frame CT/MR series are sampled, not read volumetrically.** A finding
   visible on only one unsampled slice will be missed. Raise `MAOS_MAX_IMAGES`
   or use a 3D model.
3. **No validated prior-image comparison.** Prior assessment and clinician
   updates are carried longitudinally, and repeated written observations can be
   compared, but interval change across prior image studies is not clinically
   validated.
4. **Python cannot interrupt a blocking C call**, so `MAOS_CALL_TIMEOUT`
   abandons a stuck call rather than killing it. The thread may linger until
   the underlying socket times out.
5. **Hosted inference means data leaves the machine.** Use the Ollama route
   (`MAOS_VLM_BACKEND=ollama`) for anything involving real patient data.
