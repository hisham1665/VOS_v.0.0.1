"""``python -m aos_v0.exam.review`` entry point."""

from __future__ import annotations

from .cli import review_cli_main

if __name__ == "__main__":
    raise SystemExit(review_cli_main())