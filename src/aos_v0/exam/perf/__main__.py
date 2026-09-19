"""Allow `python -m aos_v0.exam.perf`."""

import sys

from aos_v0.exam.perf.probe import probe_cli_main

if __name__ == "__main__":
    sys.exit(probe_cli_main())