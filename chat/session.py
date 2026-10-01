"""Persistent chat sessions: one folder per conversation.

Layout (``sessions/<slug>_<id>/``)::

    session.json        metadata: id, name, patient context, files, analyses,
                        rolling summary -- the resumable state
    transcript.jsonl    every event, one JSON object per line (append-only;
                        the source of truth for resume and audit)
    terminal.log        human-readable copy of everything shown on screen,
                        headed with the session id and name
    logs/pipeline.log   every pipeline log record from this session's runs
    uploads/            every attached file, de-duplicated by content hash
    reports/            one markdown + JSON report per analysis run
    memory/             retrieval chunks and cached embeddings

Writes are append-and-close (no long-lived handles), so a crash loses at most
the event being written, and a folder can be renamed on Windows mid-session.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from config import SESSIONS_DIR, SUPPORTED_DICOM_SUFFIXES, SUPPORTED_DOCUMENT_SUFFIXES, SUPPORTED_IMAGE_SUFFIXES
from core import cache

SUPPORTED_SUFFIXES = (
    SUPPORTED_IMAGE_SUFFIXES | SUPPORTED_DOCUMENT_SUFFIXES | (SUPPORTED_DICOM_SUFFIXES - {""})
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(text: str, fallback: str = "session") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or fallback


def is_supported_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in SUPPORTED_SUFFIXES:
        return True
    if suffix == "":
        # Extensionless DICOM: "DICM" magic at byte 128.
        try:
            with open(path, "rb") as handle:
                handle.seek(128)
                return handle.read(4) == b"DICM"
        except OSError:
            return False
    return False


@dataclass
class SessionFile:
    name: str  # name inside uploads/
    original: str  # where it came from
    sha: str
    size: int
    added_at: str
    kind: str  # image | document | dicom


@dataclass
class AnalysisRun:
    run: int
    at: str
    report_md: str
    report_json: str
    status: str
    triage: str
    files: list[str]
    degraded: bool = False


@dataclass
class SessionMeta:
    id: str
    name: str
    folder: str
    created_at: str
    updated_at: str
    clinical_history: str = ""
    files: list[SessionFile] = field(default_factory=list)
    analyses: list[AnalysisRun] = field(default_factory=list)
    # Rolling summary of turns that no longer fit verbatim in the context.
    summary: str = ""
    summarized_turns: int = 0  # conversation messages folded into summary
    turns: int = 0  # answered questions
    settings: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "SessionMeta":
        data = dict(data)
        data["files"] = [SessionFile(**f) for f in data.get("files", [])]
        data["analyses"] = [AnalysisRun(**a) for a in data.get("analyses", [])]
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


class Session:
    """One conversation and everything attached to it."""

    def __init__(self, folder: Path, meta: SessionMeta):
        self.folder = folder
        self.meta = meta
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ paths
    @property
    def id(self) -> str:
        return self.meta.id

    @property
    def name(self) -> str:
        return self.meta.name

    @property
    def uploads(self) -> Path:
        return self.folder / "uploads"

    @property
    def reports(self) -> Path:
        return self.folder / "reports"

    @property
    def memory_dir(self) -> Path:
        return self.folder / "memory"

    @property
    def logs_dir(self) -> Path:
        return self.folder / "logs"

    @property
    def transcript_path(self) -> Path:
        return self.folder / "transcript.jsonl"

    @property
    def terminal_log_path(self) -> Path:
        return self.folder / "terminal.log"

    @property
    def pipeline_log_path(self) -> Path:
        return self.logs_dir / "pipeline.log"

    def ensure_dirs(self) -> None:
        for directory in (self.uploads, self.reports, self.memory_dir, self.logs_dir):
            directory.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- metadata
    def save(self) -> None:
        with self._lock:
            self.meta.updated_at = _now()
            self.meta.folder = self.folder.name
            path = self.folder / "session.json"
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(asdict(self.meta), indent=2), encoding="utf-8")
            tmp.replace(path)

    # ----------------------------------------------------------- event log
    def log_event(self, kind: str, text: str = "", **data: Any) -> dict:
        """Append to transcript.jsonl and terminal.log. Returns the event."""
        event = {"ts": _now(), "session": self.id, "kind": kind, "text": text, **data}
        with self._lock:
            with open(self.transcript_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            with open(self.terminal_log_path, "a", encoding="utf-8") as handle:
                handle.write(_render_terminal_line(event))
        return event

    def events(self) -> Iterator[dict]:
        if not self.transcript_path.exists():
            return
        with open(self.transcript_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue  # a torn final line after a crash

    def conversation(self) -> list[dict]:
        """User/assistant turns in order, as ``{role, text, ts}``."""
        turns = []
        for event in self.events():
            if event.get("kind") in ("user", "assistant"):
                turns.append({"role": event["kind"], "text": event.get("text", ""),
                              "ts": event.get("ts")})
        return turns

    # -------------------------------------------------------------- uploads
    def add_file(self, source: Path) -> tuple[SessionFile, bool]:
        """Copy ``source`` into uploads/. Returns ``(record, is_new)``."""
        source = Path(source).expanduser().resolve()
        digest = cache.hash_file(source)
        with self._lock:
            for existing in self.meta.files:
                if existing.sha == digest:
                    return existing, False
            target = self.uploads / source.name
            counter = 2
            while target.exists():
                target = self.uploads / f"{source.stem}_{counter}{source.suffix}"
                counter += 1
            shutil.copy2(source, target)
            record = SessionFile(
                name=target.name,
                original=str(source),
                sha=digest,
                size=target.stat().st_size,
                added_at=_now(),
                kind=_kind_for(target),
            )
            self.meta.files.append(record)
            self.save()
            return record, True

    def record_analysis(self, run: AnalysisRun) -> None:
        with self._lock:
            self.meta.analyses.append(run)
            self.save()

    def next_run_number(self) -> int:
        return len(self.meta.analyses) + 1


def _kind_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in SUPPORTED_IMAGE_SUFFIXES:
        return "image"
    if suffix in SUPPORTED_DOCUMENT_SUFFIXES:
        return "document"
    return "dicom"


def _render_terminal_line(event: dict) -> str:
    stamp = event["ts"].replace("T", " ").replace("+00:00", "Z")
    kind = event["kind"].upper()
    body = event.get("text", "")
    extra = ""
    if event.get("attachments"):
        extra = "\n    attachments: " + ", ".join(event["attachments"])
    lines = str(body).splitlines() or [""]
    indented = "\n".join("    " + line for line in lines)
    return f"[{stamp}] [{event['session']}] {kind}{extra}\n{indented}\n"


class SessionStore:
    """Create, list, find and open sessions under ``root``."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root or SESSIONS_DIR)

    def create(self, name: str | None = None) -> Session:
        self.root.mkdir(parents=True, exist_ok=True)
        session_id = secrets.token_hex(3)
        name = (name or "").strip() or f"session-{datetime.now():%Y%m%d-%H%M}"
        folder = self.root / f"{slugify(name)}_{session_id}"
        meta = SessionMeta(
            id=session_id, name=name, folder=folder.name,
            created_at=_now(), updated_at=_now(),
        )
        session = Session(folder, meta)
        session.ensure_dirs()
        session.save()
        with open(session.terminal_log_path, "a", encoding="utf-8") as handle:
            handle.write(
                f"# Medical AOS session log\n# session id:   {session_id}\n"
                f"# session name: {name}\n# created:      {meta.created_at}\n\n"
            )
        return session

    def open(self, folder: Path) -> Session:
        meta = SessionMeta.from_dict(json.loads((folder / "session.json").read_text(encoding="utf-8")))
        session = Session(folder, meta)
        session.ensure_dirs()
        return session

    def list(self) -> list[Session]:
        if not self.root.exists():
            return []
        sessions = []
        for folder in self.root.iterdir():
            if (folder / "session.json").exists():
                try:
                    sessions.append(self.open(folder))
                except (OSError, ValueError, TypeError):
                    continue
        return sorted(sessions, key=lambda s: s.meta.updated_at, reverse=True)

    def find(self, query: str) -> Session | None:
        """Match by exact id, id prefix, exact name, or unique name substring."""
        query = query.strip().lower()
        if not query:
            return None
        sessions = self.list()
        for predicate in (
            lambda s: s.id == query,
            lambda s: s.name.lower() == query or s.folder.name.lower() == query,
            lambda s: s.id.startswith(query),
        ):
            hits = [s for s in sessions if predicate(s)]
            if len(hits) == 1:
                return hits[0]
        hits = [s for s in sessions if query in s.name.lower()]
        return hits[0] if len(hits) == 1 else None

    def rename(self, session: Session, name: str) -> Session:
        name = name.strip()
        if not name:
            return session
        target = self.root / f"{slugify(name)}_{session.id}"
        with session._lock:
            if target != session.folder:
                session.folder.rename(target)
                session.folder = target
            old = session.meta.name
            session.meta.name = name
            session.save()
            with open(session.terminal_log_path, "a", encoding="utf-8") as handle:
                handle.write(f"# renamed: {old} -> {name} at {_now()}\n")
        return session
