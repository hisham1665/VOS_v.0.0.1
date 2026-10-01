"""Shared fixtures. No network and no model weights are touched by the suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def override_runtime():
    """Temporarily change frozen RUNTIME settings, restoring them afterwards."""
    from config import RUNTIME

    original: dict = {}

    def _set(**values):
        for key, value in values.items():
            original.setdefault(key, getattr(RUNTIME, key))
            object.__setattr__(RUNTIME, key, value)

    yield _set

    for key, value in original.items():
        object.__setattr__(RUNTIME, key, value)


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, override_runtime):
    """Point the disk cache at a temp dir so tests never share state."""
    override_runtime(cache_dir=tmp_path / "cache")
    yield


@pytest.fixture
def png_factory(tmp_path):
    """Create a synthetic greyscale PNG that looks vaguely like a radiograph."""

    def _make(name: str = "chest_xray.png", size=(64, 64), shade: int = 128) -> Path:
        from PIL import Image

        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("L", size, color=shade).save(path)
        return path

    return _make


@pytest.fixture
def dicom_factory(tmp_path):
    """Write a minimal but valid DICOM file with 16-bit pixel data."""

    def _make(name: str = "scan.dcm", modality: str = "CR", rows: int = 32) -> Path:
        pydicom = pytest.importorskip("pydicom")
        import numpy as np
        from pydicom.dataset import Dataset, FileMetaDataset
        from pydicom.uid import ExplicitVRLittleEndian

        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = pydicom.uid.SecondaryCaptureImageStorage
        meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian

        dataset = Dataset()
        dataset.file_meta = meta
        dataset.PatientName = "DOE^JANE"
        dataset.PatientID = "PID-12345"
        dataset.PatientAge = "054Y"
        dataset.PatientSex = "F"
        dataset.Modality = modality
        dataset.BodyPartExamined = "CHEST"
        dataset.ViewPosition = "PA"
        dataset.Rows = rows
        dataset.Columns = rows
        dataset.SamplesPerPixel = 1
        dataset.PhotometricInterpretation = "MONOCHROME2"
        dataset.BitsAllocated = 16
        dataset.BitsStored = 16
        dataset.HighBit = 15
        dataset.PixelRepresentation = 0
        dataset.RescaleSlope = 1
        dataset.RescaleIntercept = 0

        # A gradient, so the image is not uniform (which we reject as blank).
        gradient = np.tile(
            np.linspace(0, 4000, rows, dtype="uint16"), (rows, 1)
        )
        dataset.PixelData = gradient.tobytes()

        path = tmp_path / name
        dataset.save_as(str(path), enforce_file_format=True)
        return path

    return _make


@pytest.fixture
def study_folder(tmp_path, png_factory):
    """A realistic study folder: two images, a sidecar, and junk to skip."""
    folder = tmp_path / "study"
    folder.mkdir()

    from PIL import Image

    Image.new("L", (80, 60), color=90).save(folder / "chest_xray_pa.png")
    Image.new("L", (80, 60), color=200).save(folder / "chest_xray_lateral.png")
    (folder / "patient.json").write_text(
        '{"age": "54", "sex": "F", "clinical_history": "cough, fever 3 days",'
        ' "referring_question": "rule out pneumonia"}',
        encoding="utf-8",
    )
    (folder / "notes.xyz").write_text("unsupported", encoding="utf-8")
    return folder


@pytest.fixture
def fake_capability(monkeypatch):
    """Replace a capability with a stub, so tests never call a real model."""

    def _install(name: str, fn):
        from capabilities import registry

        registry._ensure_loaded()
        monkeypatch.setitem(registry._REGISTRY, name, fn)

    return _install
