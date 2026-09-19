"""Allow `python -m aos_v0.exam.research` (module entry)."""

import sys

from aos_v0.exam.research.cli import research_cli_main

if __name__ == "__main__":
    sys.exit(research_cli_main())