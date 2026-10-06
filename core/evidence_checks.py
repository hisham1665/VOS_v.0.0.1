"""Domain-neutral exact-token checks; not semantic or clinical validation."""
from __future__ import annotations

import re

_NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\w.])")


def unsupported_numbers(description: str, source: str) -> list[str]:
    present = set(_NUMBER.findall(source.replace(",", "")))
    return sorted(set(_NUMBER.findall(description.replace(",", ""))) - present)
