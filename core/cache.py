"""Content-addressed disk cache.

Two expensive things get cached:

* preprocessed images (decode + window + resize + re-encode), and
* model outputs keyed by ``(model id, prompt, image hash)``.

Re-running a study after a crash, or re-reading the same prior imaging, then
costs a file read instead of a GPU pass or a network round trip.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Callable, TypeVar

from config import RUNTIME
from core.logging_utils import get_logger

T = TypeVar("T")
_log = get_logger("cache")
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    """One lock per key so two threads never compute the same entry twice."""
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:32]


def hash_file(path: Path, chunk: int = 1 << 20) -> str:
    """Hash a file's contents without loading it fully into memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()[:32]


def make_key(*parts: Any) -> str:
    """Build a stable cache key from arbitrary JSON-serialisable parts."""
    payload = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _path_for(namespace: str, key: str, suffix: str) -> Path:
    directory = RUNTIME.cache_dir / namespace / key[:2]
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{key}{suffix}"


def get_or_compute_json(namespace: str, key: str, compute: Callable[[], Any]) -> Any:
    """Return a cached JSON value, computing and storing it on a miss."""
    if not RUNTIME.cache_enabled:
        return compute()

    path = _path_for(namespace, key, ".json")
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            _log.debug("corrupt cache entry discarded: %s", path)
            path.unlink(missing_ok=True)

    with _lock_for(f"{namespace}:{key}"):
        # Another thread may have populated it while we waited on the lock.
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                path.unlink(missing_ok=True)

        value = compute()
        try:
            path.write_text(
                json.dumps(value, ensure_ascii=False, default=str), encoding="utf-8"
            )
        except OSError as exc:
            _log.debug("cache write failed (%s); continuing uncached", exc)
        return value


def get_or_compute_bytes(
    namespace: str, key: str, compute: Callable[[], bytes], suffix: str = ".bin"
) -> Path:
    """Return the path to cached bytes, writing them on a miss.

    Returning a path (not the bytes) lets callers stream large images straight
    off disk instead of holding them in memory.
    """
    path = _path_for(namespace, key, suffix)
    if not RUNTIME.cache_enabled:
        path.write_bytes(compute())
        return path

    if path.exists() and path.stat().st_size > 0:
        return path

    with _lock_for(f"{namespace}:{key}"):
        if path.exists() and path.stat().st_size > 0:
            return path
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(compute())
        tmp.replace(path)  # atomic: readers never see a half-written file
        return path


def clear(namespace: str | None = None) -> int:
    """Delete cached entries. Returns the number of files removed."""
    root = RUNTIME.cache_dir / namespace if namespace else RUNTIME.cache_dir
    if not root.exists():
        return 0
    removed = 0
    for item in root.rglob("*"):
        if item.is_file():
            item.unlink(missing_ok=True)
            removed += 1
    return removed


def read_json(namespace: str, key: str) -> Any | None:
    """Return a cached value, or ``None`` on a miss. Never computes."""
    if not RUNTIME.cache_enabled:
        return None
    path = _path_for(namespace, key, ".json")
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        path.unlink(missing_ok=True)
        return None


def write_json(namespace: str, key: str, value: Any) -> None:
    """Store a value. Failures are logged and ignored -- the cache is optional."""
    if not RUNTIME.cache_enabled:
        return
    path = _path_for(namespace, key, ".json")
    try:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(value, ensure_ascii=False, default=str), encoding="utf-8"
        )
        tmp.replace(path)
    except OSError as exc:
        _log.debug("cache write failed (%s); continuing uncached", exc)
