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
_FORMATTER = logging.Formatter(
    fmt="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)


def configure(level: str = "INFO") -> None:
    """Install a single stderr handler. Safe to call repeatedly."""
    global _configured
    with _lock:
        if _configured:
            return
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_FORMATTER)
        root = logging.getLogger("maos")
        root.setLevel(getattr(logging, level.upper(), logging.INFO))
        root.addHandler(handler)
        root.propagate = False
        _configured = True


def set_console_logging(enabled: bool) -> None:
    """Attach or detach the stderr handler.

    A full-screen TUI owns the terminal; a stray log line written to stderr
    tears the display. The TUI turns console logging off and routes records to
    the session's log file and status line instead.
    """
    configure()
    root = logging.getLogger("maos")
    console = [
        h for h in root.handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        and getattr(h, "stream", None) in (sys.stderr, sys.__stderr__)
    ]
    if enabled and not console:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_FORMATTER)
        root.addHandler(handler)
    elif not enabled:
        for handler in console:
            root.removeHandler(handler)


def add_handler(handler: logging.Handler) -> None:
    configure()
    if handler.formatter is None:
        handler.setFormatter(_FORMATTER)
    logging.getLogger("maos").addHandler(handler)


def remove_handler(handler: logging.Handler) -> None:
    logging.getLogger("maos").removeHandler(handler)


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
