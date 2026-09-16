# TESTING.md — Testing Guide

## Setup

```bash
python -m pytest tests/ -v
```

No special test framework configuration. Uses `unittest.TestCase` throughout.

## Test Files

### test_event_integration.py (35 lines)

**Class: `EventIntegrationTests`**

| Test | What it verifies |
|------|------------------|
| `test_executor_publishes_real_agent_lifecycle` | GraphExecutor emits AGENT_STARTED, AGENT_SELECTED, AGENT_COMPLETED events for a one-node summarization graph |
| `test_failure_manager_publishes_recovery` | FailureManager emits RECOVERY_STARTED/RECOVERY_COMPLETED when run_fn returns empty output; output contains "UNAVAILABLE" |

### test_document.py (134 lines)

**Class: `DocumentCapabilityTests`**

| Test | What it verifies |
|------|------------------|
| `test_extracts_text_from_pdf` | document.run() extracts text from a hand-crafted PDF |
| `test_missing_file_raises` | document.run() raises FileNotFoundError for missing path |
| `test_artifact_registers_document_modality` | ArtifactManager yields modality="document" for PDFs |
| `test_document_flag_implies_document_modality` | required_input_modality(["document.extraction"]) returns "document" |
| `test_executor_extracts_pdf_into_dependent_node_input` | End-to-end: document_extraction → summarization nodes; dependent receives extracted text, not file path |

### test_medical.py (377 lines)

**12 coverage scenarios. Class: `MedicalFolderTests`**

| Test | What it verifies |
|------|------------------|
| `test_nested_folders_preserve_relative_paths` | scan_folder keeps subdirectory structure in relative_path |
| `test_unsupported_file_reported_not_crash` | Unknown files get category="unsupported" without crashing |
| `test_xray_folder_route_and_chart_flag` | X-ray folder → chart contains "Patient Chart" + finding + physician note |
| `test_blood_folder_lab_values_and_status` | Lab output shows Glucose/HIGH/LOW; chart has values; rows have source_file+page |
| `test_xray_plus_blood_parallel_branches_merge` | Parallel lab+image branches merge into one chart |
| `test_prescription_report_and_image_merge` | Prescription, discharge report, and image all appear in final chart |
| `test_corrupt_files_do_not_fabricate_values` | Broken PDF/PNG → graceful messages, never invented numbers |
| `test_ner_failure_falls_back_to_deterministic_parser` | No HF_TOKEN → NER auth failure → deterministic parser fallback |
| `test_agent_failure_triggers_failure_manager_recovery` | ProviderError → node status="degraded", routing_mode="dna" |
| `test_lab_status_derived_from_report_range` | parse_lab_lines marks HIGH/LOW/NORMAL from report's own ranges |
| `test_chart_contains_all_sections_and_disclaimer` | Chart has all sections + disclaimer text |
| `test_build_medical_job_embeds_manifest` | Job string contains user prompt + "MEDICAL WORKFLOW" + "FOLDER:" + manifest |

### test_interactive.py (85 lines)

**Class: `InteractiveTests`**

| Test | What it verifies |
|------|------------------|
| `test_command_registry_parses_and_reports_unknown_commands` | /ping → args passthrough; plain text → None; unknown → error |
| `test_command_registry_preserves_windows_upload_path` | Quoted Windows paths preserved intact |
| `test_session_records_execution_and_recovery_events` | Session.summary() counts failures and recoveries |
| `test_event_renderer_uses_actual_score_payload` | format_event renders real score from candidate payload |

**Class: `InteractiveSubmissionTests`**

| Test | What it verifies |
|------|------------------|
| `test_submit_defaults_to_latest_uploaded_document` | No IDs → latest document artifact path used |
| `test_submit_explicit_ids_still_win` | Explicit artifact_ids override default |
| `test_submit_with_no_uploads_sends_no_inputs` | No uploads → inputs is None |

### test_artifacts.py (46 lines)

**Class: `ArtifactManagerTests`**

| Test | What it verifies |
|------|------------------|
| `test_registers_pdf_with_id_and_metadata` | PDF gets art_ ID, type="document", metadata with .pdf |
| `test_registers_image_and_audio` | Image/audio get matching artifact_type + modality |
| `test_unknown_files_are_represented_not_rejected` | .bin registered as "unknown", not rejected |
| `test_missing_file_is_actionable` | Raises ArtifactError matching "File not found" |
| `test_multiple_files_and_remove` | register_many lists 2; remove with delete deletes file |

## Writing New Tests

### Pattern

```python
import unittest

class MyFeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_something(self):
        # Arrange
        # Act
        # Assert
        self.assertEqual(actual, expected)
```

### Stubbing Resources

For testing without real LLM calls, create stub run functions:

```python
def stub_run(text, instruction=None):
    return f"stub output for: {text[:50]}"

manifest = CapabilityManifest(
    resource_id="stub",
    resource_class="llm",
    capabilities=["text.summarization"],
    input_schema=IOSchema(type="text", format="str"),
    output_schema=IOSchema(type="text", format="str"),
    cost_model=CostModel(unit="per_call", estimate_usd=0.0),
    latency_model=LatencyModel(),
    quality_priors={"text.summarization": 0.8},
    availability=Availability(),
    risk_class="low",
    metadata={},
)
registry.register(manifest, stub_run)
```

### Building Test Graphs

```python
from aos_v0.core.models import Node, Graph

graph = Graph(
    job="Test job",
    nodes=[
        Node(id="a", description="Step 1", capability="web_search"),
        Node(id="b", description="Step 2", capability="summarization", depends_on=["a"]),
    ],
)
```

### Event Sink for Assertions

```python
events = []
def capture_event(event):
    events.append(event)

# Pass to GraphExecutor
executor = GraphExecutor(registry, event_sink=capture_event)
```

## Coverage Notes

- **Not covered:** LLM integration tests (require real API keys), TUI rendering tests (require textual)
- **Covered:** All deterministic logic, event emission, failure recovery, artifact management, medical workflow, document extraction, command parsing, interactive submission
