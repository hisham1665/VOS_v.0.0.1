"""Allow `python -m aos_v0.exam.security`."""

import sys

from aos_v0.exam.security.cli import security_cli_main

if __name__ == "__main__":
    sys.exit(security_cli_main())