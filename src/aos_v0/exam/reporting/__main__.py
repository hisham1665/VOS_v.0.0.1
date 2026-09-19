"""``python -m aos_v0.exam.reporting`` entry point."""

from __future__ import annotations

from .cli import report_cli_main

if __name__ == "__main__":
    raise SystemExit(report_cli_main())