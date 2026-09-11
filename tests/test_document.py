import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("HF_TOKEN", "")

from aos_v0.capabilities.document import run
from aos_v0.core.artifacts import ArtifactManager
from aos_v0.core.capability_registry import required_input_modality
from aos_v0.core.models import CapabilityDNA, DNAOrdinals, Graph, Node
from aos_v0.agents.graph_executor import GraphExecutor
from aos_v0.services.resource_registration import build_hf_enabled_registry


MINIMAL_PDF = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 240 >>
stream
BT /F1 18 Tf 50 700 Td (AOS PDF extraction test) Tj ET
BT /F1 12 Tf 50 680 Td (This is the second line of test content inside the document.) Tj ET
BT /F1 12 Tf 50 660 Td (It carries enough words so the extracted output is not mistaken) Tj ET
BT /F1 12 Tf 50 640 Td (for a degenerate empty result by the failure manager. Third line.) Tj ET
BT /F1 12 Tf 50 620 Td (A fourth line completes the fixture with more extractable text.) Tj ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000222 00000 n
0000000368 00000 n
trailer
<< /Size 6 /Root 1 0 R >>
startxref
403
%%EOF
"""


class DocumentCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf_path = self.root / "sample.pdf"
        self.pdf_path.write_bytes(MINIMAL_PDF)

    def tearDown(self):
        self.temp.cleanup()

    def test_extracts_text_from_pdf(self):
        content = run(str(self.pdf_path))
        self.assertIn("AOS PDF extraction test", content)
        self.assertIn("second line of test content", content)
        self.assertIn("failure manager", content)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            run(str(self.root / "does_not_exist.pdf"))

    def test_artifact_registers_document_modality(self):
        manager = ArtifactManager(self.root / "stored")
        artifact = manager.register(self.pdf_path)
        self.assertEqual(artifact.modality, "document")
        self.assertEqual(artifact.artifact_type, "document")

    def test_document_flag_implies_document_modality(self):
        self.assertEqual(
            required_input_modality(["document.extraction"], "document_extraction"),
            "document",
        )

    def test_executor_extracts_pdf_into_dependent_node_input(self):
        registry = build_hf_enabled_registry()
        graph = Graph(
            job="Summarize the PDF",
            nodes=[
                Node(
                    id="a",
                    description="Extract all text from the uploaded PDF document",
                    capability="document_extraction",
                    dna=CapabilityDNA(
                        flags=["document.extraction"],
                        ordinals=DNAOrdinals(reasoning_depth=1, tool_complexity=1),
                        extracted_by="test",
                    ),
                ),
                Node(
                    id="b",
                    description="Read the document text",
                    capability="summarization",
                    depends_on=["a"],
                    dna=CapabilityDNA(
                        flags=["text.summarization"],
                        ordinals=DNAOrdinals(reasoning_depth=1),
                        extracted_by="test",
                    ),
                ),
            ],
        )
        executor = GraphExecutor(registry)
        # Override the summarization run_fn so no API is called.
        registry._run_fns["summarization"] = (
            lambda text, instruction=None: f"received:{text[:200]}"
        )
        executor.run(graph, inputs={"document": str(self.pdf_path)})

        extract_node = graph.nodes[0]
        down_node = graph.nodes[1]
        self.assertEqual(extract_node.status, "done")
        self.assertIn("AOS PDF extraction test", extract_node.output)
        # Dependent node must receive the extracted text, not a file path.
        self.assertIn("IMPORTANT CONTEXT", down_node.input)
        self.assertIn("AOS PDF extraction test", down_node.input)
        self.assertNotIn(".pdf", down_node.input)


if __name__ == "__main__":
    unittest.main()