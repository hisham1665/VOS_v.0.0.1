"""Allow `python -m aos_v0.exam.provenance`."""

import sys

from aos_v0.exam.provenance.provenance import provenance_cli_main

if __name__ == "__main__":
    sys.exit(provenance_cli_main())