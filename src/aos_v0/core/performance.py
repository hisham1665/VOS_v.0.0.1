"""Content-free routing history measures invocation reliability, not answer accuracy."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

_lock = threading.RLock()


class PerformanceStore:
    def __init__(self, path: str | Path = ".cache/aos-performance.json", enabled: bool = True):
        self.path = Path(path)
        self.enabled = enabled
        self.records: dict = {}
        if enabled:
            try:
                self.records = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass

    def get(self, resource_id: str) -> dict:
        with _lock:
            return dict(self.records.get(resource_id, {}))

    def observe(self, resource_id: str, success: bool, elapsed_ms: float) -> None:
        with _lock:
            record = self.records.setdefault(resource_id, {"calls": 0, "successes": 0, "latency_ms": elapsed_ms})
            record["calls"] += 1
            record["successes"] += int(success)
            record["latency_ms"] = 0.2 * elapsed_ms + 0.8 * record["latency_ms"]
            record["cooldown_until"] = 0 if success else time.time() + 15
            if not self.enabled:
                return
            temporary = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as file:
                    temporary = file.name
                    json.dump(self.records, file)
                os.replace(temporary, self.path)
            except OSError:
                if temporary:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass
