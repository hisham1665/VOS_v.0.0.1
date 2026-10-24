import tempfile
import time
import unittest
from pathlib import Path

from aos_v0.core.artifacts import ArtifactManager
from aos_v0.core.capability_registry import CapabilityManifest, CapabilityRegistry, IOSchema
from aos_v0.core.memory_admission import HeuristicController
from aos_v0.core.memory_retrieval import ContextBudget, MemoryRetriever
from aos_v0.core.models import CapabilityDNA, Node
from aos_v0.core.shared_memory import MemoryCandidate, SharedMemoryBank, compute_input_hash
from aos_v0.settings import load_settings


class V1CoreTests(unittest.TestCase):
    def test_operator_configuration_and_namespaced_capability_contract(self):
        settings = load_settings()
        self.assertEqual(settings.schema_version, 1)
        self.assertIn("text", settings.models)
        dna = CapabilityDNA(flags=["medicine.triage"])
        self.assertEqual(dna.flags, ["medicine.triage"])

    def test_memory_is_selective_bounded_and_key_only(self):
        bank = SharedMemoryBank("request", max_entries=2, max_total_bytes=600)
        candidate = MemoryCandidate(
            node_id="source", capability="document_extraction", description="lab report",
            value="Important observation " * 50, agent_input="report.pdf",
        )
        entry = bank.admit(candidate)
        self.assertFalse(hasattr(bank.keys()[0], "value"))
        self.assertEqual(bank.lookup_reusable("document_extraction", compute_input_hash("document_extraction", "report.pdf")).id, entry.id)

        node = Node(id="consumer", description="summarize lab report", capability="summarization")
        recall = MemoryRetriever(ContextBudget(total_chars=240, per_entry_chars=80)).select(node, bank)
        self.assertLessEqual(len(recall.render()), 240)
        self.assertEqual(entry.recall_count, 1)

        bad = MemoryCandidate(node_id="failed", capability="web_search", status="failed", value="x" * 500)
        self.assertFalse(HeuristicController().decide(bad, bank).admitted)

    def test_registry_rejects_incompatible_input_and_skill(self):
        registry = CapabilityRegistry()
        registry.register(
            CapabilityManifest(
                resource_id="text-only", resource_class="llm", capabilities=["text.summarization"],
                input_schema=IOSchema(type="text"),
            ),
            lambda text, instruction=None: text,
        )
        dna = CapabilityDNA(flags=["vision.understanding"])
        with self.assertRaisesRegex(RuntimeError, "input type text incompatible with image"):
            registry.select(dna, required_modality="image")

    def test_artifacts_record_content_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "note.txt"
            source.write_text("stable evidence", encoding="utf-8")
            artifact = ArtifactManager(Path(directory) / "stored").register(source)
            self.assertEqual(len(artifact.metadata["sha256"]), 64)
            self.assertEqual(artifact.artifact_type, "text")


if __name__ == "__main__":
    unittest.main()
