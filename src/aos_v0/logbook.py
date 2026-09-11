"""Terminal log capture for debugging.

Replaces sys.stdout with a Tee that writes every print() to both the
real terminal and a timestamped text file in the project's log/ folder.
One file per run() invocation.

Usage::

    from aos_v0.logbook import SessionLogger

    with SessionLogger("my-prompt") as logger:
        print("this goes to terminal AND log file")
    # logger.log_path has the file that was written
"""

from __future__ import annotations

import io
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LOG_DIR = _PROJECT_ROOT / "log"


class Tee(io.TextIOBase):
    """Multiplex writes to the original stdout and a log file.

    Thread-safe: uses a lock so interleaved prints from ThreadPoolExecutor
    nodes don't corrupt the log file.
    """

    def __init__(self, original: io.TextIOBase, log_file: io.BufferedWriter) -> None:
        self._original = original
        self._log_file = log_file
        self._lock = threading.Lock()

    def write(self, text: str) -> int:
        with self._lock:
            n = self._original.write(text)
            try:
                self._log_file.write(text.encode("utf-8", errors="replace"))
                self._log_file.flush()
            except ValueError:
                pass  # log file already closed during shutdown
            return n

    def flush(self) -> None:
        with self._lock:
            self._original.flush()
            try:
                self._log_file.flush()
            except ValueError:
                pass  # log file already closed during shutdown

    # Delegate attribute access (encoding, isatty, etc.) to original stdout
    def __getattr__(self, name: str):
        return getattr(self._original, name)


class SessionLogger:
    """Context manager that captures a single run() into a log file.

    File name format: ``log/run_YYYY-MM-DD_HHMMSS_<short-id>.txt``

    Attributes:
        log_path: Absolute path of the log file written.
        prompt:   The user prompt that initiated this session.
    """

    def __init__(self, prompt: str = "", tag: str = "run") -> None:
        self.prompt = prompt
        self.tag = tag
        self.log_path: Path | None = None
        self._original_stdout = None
        self._tee: Tee | None = None
        self._log_file: io.BufferedWriter | None = None

    def start(self) -> Path:
        """Begin capturing. Returns the log file path."""
        _LOG_DIR.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        uid = str(int(time.time() * 1000))[-4:]
        filename = f"{self.tag}_{ts}_{uid}.txt"
        self.log_path = _LOG_DIR / filename

        self._log_file = open(self.log_path, "wb")
        self._original_stdout = sys.stdout

        # Write session header
        header = (
            f"{'=' * 72}\n"
            f"AOS Session Log\n"
            f"Timestamp : {datetime.now().isoformat()}\n"
            f"Prompt    : {self.prompt}\n"
            f"{'=' * 72}\n\n"
        )
        self._log_file.write(header.encode("utf-8", errors="replace"))
        self._log_file.flush()

        self._tee = Tee(self._original_stdout, self._log_file)
        sys.stdout = self._tee  # type: ignore[assignment]
        return self.log_path

    def stop(self) -> None:
        """Stop capturing and restore original stdout."""
        if self._original_stdout is not None:
            sys.stdout = self._original_stdout  # type: ignore[assignment]
        if self._log_file:
            self._log_file.close()
            self._log_file = None

    def __enter__(self) -> SessionLogger:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
