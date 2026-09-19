"""Module entry point: `python3 -m aos_v0.exam.batch <exam.json> <answers>`."""

from .driver import batch_cli_main

if __name__ == "__main__":
    raise SystemExit(batch_cli_main())