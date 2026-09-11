"""Universal, storage-focused artifact boundary for AOS."""

from __future__ import annotations

import mimetypes
import shutil
import uuid
from pathlib import Path
from typing import Iterable, Optional

from aos_v0.core.models import Artifact


class ArtifactError(ValueError):
    pass


class ArtifactManager:
    """Registers files without coupling their lifecycle to an agent or UI."""

    DEFAULT_MAX_SIZE_BYTES = 100 * 1024 * 1024
    _EXTENSIONS = {
        "document": {".pdf", ".doc", ".docx", ".odt", ".rtf"},
        "text": {".txt", ".md", ".rst", ".json", ".xml", ".yaml", ".yml"},
        "image": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"},
        "audio": {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".wma"},
        "video": {".mp4", ".mov", ".mkv", ".avi", ".webm"},
        "dataset": {".csv", ".tsv", ".xlsx", ".xls", ".parquet"},
        "archive": {".zip", ".tar", ".gz", ".bz2", ".7z"},
    }

    def __init__(
        self, storage_dir: str | Path, max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES,
        copy_files: bool = True,
    ) -> None:
        self.storage_dir = Path(storage_dir)
        self.max_size_bytes = max_size_bytes
        self.copy_files = copy_files
        self._artifacts: dict[str, Artifact] = {}

    def register(self, file_path: str | Path) -> Artifact:
        source = Path(file_path).expanduser().resolve()
        if not source.is_file():
            raise ArtifactError(f"File not found: {file_path}")
        if not source.stat().st_size:
            raise ArtifactError(f"File is empty: {file_path}")
        if source.stat().st_size > self.max_size_bytes:
            raise ArtifactError(f"File exceeds {self.max_size_bytes} byte size limit: {source.name}")
        try:
            with source.open("rb"):
                pass
        except OSError as exc:
            raise ArtifactError(f"File is not readable: {file_path}") from exc

        artifact_id = f"art_{uuid.uuid4().hex[:12]}"
        target = source
        if self.copy_files:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            target = self.storage_dir / f"{artifact_id}_{source.name}"
            shutil.copy2(source, target)
        category = self.detect_type(source)
        mime_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        artifact = Artifact(
            id=artifact_id, name=source.name, modality=self._modality(category),
            artifact_type=category, mime_type=mime_type, size=source.stat().st_size,
            path=str(target), metadata=self.get_metadata(source, category),
        )
        self._artifacts[artifact.id] = artifact
        return artifact

    def register_many(self, paths: Iterable[str | Path]) -> list[Artifact]:
        return [self.register(path) for path in paths]

    def get(self, artifact_id: str) -> Optional[Artifact]:
        return self._artifacts.get(artifact_id)

    def list(self) -> list[Artifact]:
        return list(self._artifacts.values())

    def remove(self, artifact_id: str, delete_stored_file: bool = False) -> bool:
        artifact = self._artifacts.pop(artifact_id, None)
        if artifact is None:
            return False
        if delete_stored_file and self.copy_files and artifact.path:
            path = Path(artifact.path)
            if path.is_file() and path.parent == self.storage_dir.resolve():
                path.unlink()
        return True

    def cleanup(self) -> int:
        """Remove manager-owned stored copies and forget their records."""
        artifact_ids = list(self._artifacts)
        for artifact_id in artifact_ids:
            self.remove(artifact_id, delete_stored_file=True)
        return len(artifact_ids)

    def detect_type(self, path: str | Path) -> str:
        suffix = Path(path).suffix.lower()
        for category, extensions in self._EXTENSIONS.items():
            if suffix in extensions:
                return category
        return "unknown"

    def get_metadata(self, path: str | Path, category: Optional[str] = None) -> dict:
        item = Path(path)
        return {"extension": item.suffix.lower(), "category": category or self.detect_type(item)}

    @staticmethod
    def _modality(category: str) -> str:
        if category in {"audio", "image", "text", "document"}:
            return category
        return "text"
