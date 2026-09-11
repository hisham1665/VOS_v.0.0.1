import tempfile
import unittest
from pathlib import Path

from aos_v0.core.artifacts import ArtifactError, ArtifactManager


class ArtifactManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manager = ArtifactManager(self.root / "stored")

    def tearDown(self):
        self.temp.cleanup()

    def make_file(self, name, contents=b"content"):
        path = self.root / name
        path.write_bytes(contents)
        return path

    def test_registers_pdf_with_id_and_metadata(self):
        artifact = self.manager.register(self.make_file("paper.pdf"))
        self.assertTrue(artifact.id.startswith("art_"))
        self.assertEqual(artifact.artifact_type, "document")
        self.assertEqual(artifact.metadata["extension"], ".pdf")
        self.assertTrue(Path(artifact.path).is_file())

    def test_registers_image_and_audio(self):
        image, audio = self.manager.register_many([self.make_file("chart.png"), self.make_file("note.wav")])
        self.assertEqual((image.artifact_type, image.modality), ("image", "image"))
        self.assertEqual((audio.artifact_type, audio.modality), ("audio", "audio"))

    def test_unknown_files_are_represented_not_rejected(self):
        artifact = self.manager.register(self.make_file("payload.bin"))
        self.assertEqual(artifact.artifact_type, "unknown")

    def test_missing_file_is_actionable(self):
        with self.assertRaisesRegex(ArtifactError, "File not found"):
            self.manager.register(self.root / "missing.pdf")

    def test_multiple_files_and_remove(self):
        artifacts = self.manager.register_many([self.make_file("a.txt"), self.make_file("b.csv")])
        self.assertEqual(len(self.manager.list()), 2)
        self.assertTrue(self.manager.remove(artifacts[0].id, delete_stored_file=True))
        self.assertIsNone(self.manager.get(artifacts[0].id))
