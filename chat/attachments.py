"""Find file references in a chat message.

Accepted forms, all of which may appear anywhere in the text:

* ``@path/to/file.pdf`` and ``@"path with spaces/scan.png"``  (explicit)
* ``"C:\\Users\\me\\Desktop\\x ray.jpg"`` or ``'...'``  (what Windows Terminal
  and most terminals insert when a file is dragged onto the window)
* a bare token that is an existing path, e.g. ``C:\\scans\\ct.jpg``

A directory expands to every supported file inside it (recursively), so a
whole study folder can be attached at once. Referenced paths are removed from
the message; what remains is the user's question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from chat.session import is_supported_file

_MAX_FILES_PER_FOLDER = 500

_PATTERNS = (
    re.compile(r'@"([^"]+)"'),
    re.compile(r"@'([^']+)'"),
    re.compile(r'"([^"]+)"'),
    re.compile(r"'([^']+)'"),
    re.compile(r"@(\S+)"),
    re.compile(r"(?<!\S)((?:[A-Za-z]:[\\/]|\\\\|/|~[\\/]|\.{1,2}[\\/])\S+)"),
)


@dataclass
class ParsedMessage:
    text: str
    files: list[Path] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)  # referenced but unusable


def parse_message(raw: str, cwd: Path | None = None) -> ParsedMessage:
    cwd = cwd or Path.cwd()
    text = raw
    files: list[Path] = []
    rejected: list[str] = []

    for pattern in _PATTERNS:
        explicit = pattern.pattern.startswith("@")

        def _take(match: re.Match) -> str:
            candidate = _resolve(match.group(1), cwd)
            if candidate is None:
                if explicit:
                    rejected.append(f"{match.group(1)} (not found)")
                    return " "
                return match.group(0)  # just a quoted phrase, leave it
            found, bad = _expand(candidate)
            files.extend(found)
            rejected.extend(bad)
            return " "

        text = pattern.sub(_take, text)

    unique: list[Path] = []
    for path in files:
        if path not in unique:
            unique.append(path)
    return ParsedMessage(text=re.sub(r"\s{2,}", " ", text).strip(), files=unique, rejected=rejected)


def _resolve(token: str, cwd: Path) -> Path | None:
    token = token.strip().rstrip(",;")
    if not token:
        return None
    path = Path(token).expanduser()
    if not path.is_absolute():
        path = cwd / path
    try:
        return path.resolve() if path.exists() else None
    except OSError:
        return None


def _expand(path: Path) -> tuple[list[Path], list[str]]:
    if path.is_file():
        if is_supported_file(path):
            return [path], []
        return [], [f"{path.name} (unsupported type {path.suffix or 'none'})"]
    found = []
    for child in sorted(path.rglob("*")):
        if child.is_file() and not child.name.startswith(".") and is_supported_file(child):
            found.append(child)
            if len(found) >= _MAX_FILES_PER_FOLDER:
                return found, [f"{path} (stopped after {_MAX_FILES_PER_FOLDER} files)"]
    return found, ([] if found else [f"{path} (no supported files inside)"])
