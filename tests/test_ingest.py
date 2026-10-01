"""Ingest: folder walking, DICOM decoding, de-identification, preprocessing."""

from __future__ import annotations

import pytest

from core.errors import StudyError
from ingest.study_loader import load_study
from models import Modality


class TestStudyLoading:
    def test_loads_images_and_sidecar_context(self, study_folder):
        study = load_study(study_folder)

        assert len(study.images) == 2
        assert study.patient.age == "54"
        assert study.patient.clinical_history == "cough, fever 3 days"
        assert study.patient.referring_question == "rule out pneumonia"

    def test_unsupported_files_are_skipped_not_fatal(self, study_folder):
        study = load_study(study_folder)
        assert any("notes.xyz" in entry for entry in study.skipped_files)
        assert len(study.images) == 2  # the real images still loaded

    def test_corrupt_image_does_not_abort_the_study(self, study_folder):
        (study_folder / "corrupt.png").write_bytes(b"not a png at all")

        study = load_study(study_folder)

        assert len(study.images) == 2
        assert any("corrupt.png" in entry for entry in study.skipped_files)

    def test_duplicate_images_are_deduplicated(self, study_folder):
        import shutil

        shutil.copy(study_folder / "chest_xray_pa.png", study_folder / "copy.png")

        study = load_study(study_folder)

        assert len(study.images) == 2
        assert any("duplicate" in entry for entry in study.skipped_files)

    def test_missing_folder_raises(self, tmp_path):
        with pytest.raises(StudyError, match="does not exist"):
            load_study(tmp_path / "nope")

    def test_folder_without_images_raises(self, tmp_path):
        folder = tmp_path / "empty_study"
        folder.mkdir()
        (folder / "readme.xyz").write_text("nothing here", encoding="utf-8")

        with pytest.raises(StudyError, match="No readable images"):
            load_study(folder)

    def test_single_file_is_a_valid_study(self, png_factory):
        path = png_factory("solo_chest_xray.png")
        study = load_study(path)
        assert len(study.images) == 1

    def test_nested_folders_are_walked(self, tmp_path):
        from PIL import Image

        nested = tmp_path / "study" / "series1" / "sub"
        nested.mkdir(parents=True)
        Image.new("L", (32, 32), color=10).save(nested / "slice1.png")
        Image.new("L", (32, 32), color=20).save(nested / "slice2.png")

        study = load_study(tmp_path / "study")

        assert len(study.images) == 2
        assert all("series1" in img.image_id for img in study.images)

    def test_large_studies_are_sampled(self, tmp_path, override_runtime):
        from PIL import Image

        override_runtime(max_images_per_study=5)
        folder = tmp_path / "ct_series"
        folder.mkdir()
        for i in range(20):
            Image.new("L", (32, 32), color=i * 5).save(folder / f"slice{i:03d}.png")

        study = load_study(folder)

        assert len(study.images) == 5
        assert any("sampled" in w for w in study.warnings)

    def test_modality_inferred_from_filename(self, tmp_path):
        from PIL import Image

        folder = tmp_path / "s"
        folder.mkdir()
        Image.new("L", (32, 32), color=60).save(folder / "brain_mri_t1.png")

        study = load_study(folder)
        assert study.images[0].modality is Modality.MRI

    def test_explicit_history_overrides_nothing_but_fills_gaps(self, tmp_path):
        from PIL import Image

        folder = tmp_path / "s"
        folder.mkdir()
        Image.new("L", (32, 32), color=60).save(folder / "cxr.png")

        study = load_study(folder, clinical_history="smoker, 20 pack-years")
        assert study.patient.clinical_history == "smoker, 20 pack-years"


class TestDicom:
    def test_dicom_is_decoded_with_metadata(self, dicom_factory):
        path = dicom_factory("chest.dcm", modality="CR")
        study = load_study(path)

        image = study.images[0]
        assert image.is_dicom
        assert image.modality is Modality.XRAY
        assert image.body_part == "CHEST"
        assert image.view == "PA"

    def test_phi_is_stripped_by_default(self, dicom_factory):
        path = dicom_factory("chest.dcm")
        study = load_study(path)

        metadata = study.images[0].metadata
        assert metadata["deidentified"] is True
        assert "PatientName" not in metadata
        assert "PatientID" not in metadata
        # Clinically useful, non-identifying fields survive.
        assert metadata["PatientSex"] == "F"

    def test_ct_modality_maps_correctly(self, dicom_factory):
        path = dicom_factory("ct.dcm", modality="CT")
        study = load_study(path)
        assert study.images[0].modality is Modality.CT

    def test_blank_dicom_is_rejected(self, tmp_path):
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
        dataset.Modality = "CR"
        dataset.Rows = dataset.Columns = 16
        dataset.SamplesPerPixel = 1
        dataset.PhotometricInterpretation = "MONOCHROME2"
        dataset.BitsAllocated = dataset.BitsStored = 16
        dataset.HighBit = 15
        dataset.PixelRepresentation = 0
        dataset.PixelData = np.zeros((16, 16), dtype="uint16").tobytes()

        path = tmp_path / "blank.dcm"
        dataset.save_as(str(path), enforce_file_format=True)

        with pytest.raises(StudyError):
            load_study(path)


class TestPreprocessing:
    def test_oversized_images_are_downscaled(self, tmp_path, override_runtime):
        from PIL import Image

        from ingest.preprocess import prepare_image

        override_runtime(max_image_edge=128)
        source = tmp_path / "huge.png"
        Image.new("L", (2000, 1000), color=100).save(source)

        prepared, info = prepare_image(source)

        assert info["prepared_size"] == [128, 64]
        assert prepared.stat().st_size < source.stat().st_size
        assert any("downsampled" in w for w in info["warnings"])

    def test_second_call_hits_the_cache(self, tmp_path):
        from PIL import Image

        from ingest.preprocess import prepare_image

        source = tmp_path / "x.png"
        Image.new("L", (300, 300), color=40).save(source)

        first, _ = prepare_image(source)
        second, info = prepare_image(source)

        assert first == second
        assert info.get("cached") is True

    def test_identical_content_shares_one_cache_entry(self, tmp_path):
        from PIL import Image

        from ingest.preprocess import prepare_image

        a, b = tmp_path / "a.png", tmp_path / "b.png"
        Image.new("L", (100, 100), color=77).save(a)
        Image.new("L", (100, 100), color=77).save(b)

        assert prepare_image(a)[0] == prepare_image(b)[0]
