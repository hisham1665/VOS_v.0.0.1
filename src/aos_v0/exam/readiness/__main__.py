"""Allow `python -m aos_v0.exam.readiness`."""

import sys

from aos_v0.exam.readiness.checker import readiness_cli_main

if __name__ == "__main__":
    sys.exit(readiness_cli_main())