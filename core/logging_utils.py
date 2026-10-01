"""Thread-safe, timestamped logging shared by every module.

The executor runs nodes concurrently, so plain ``print`` interleaves badly.
All modules log through :func:`get_logger` instead.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from contextlib import contextmanager

_configured = False
_lock = threading.Lock()


def configure(level: str = "INFO") -> None:
    """Install a single stderr handler. Safe to call repeatedly."""
    global _configured
    with _lock:
        if _configured:
            return
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        root = logging.getLogger("maos")
        root.setLevel(getattr(logging, level.upper(), logging.INFO))
        root.addHandler(handler)
        root.propagate = False
        _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger, configuring the root handler on first use."""
    from config import RUNTIME

    configure(RUNTIME.log_level)
    return logging.getLogger(f"maos.{name}")


@contextmanager
def timed(logger: logging.Logger, label: str, level: int = logging.INFO):
    """Log how long a block took. Used to find the slow stages in a run."""
    start = time.perf_counter()
    try:
        yield
    finally:
        logger.log(level, "%s took %.2fs", label, time.perf_counter() - start)
