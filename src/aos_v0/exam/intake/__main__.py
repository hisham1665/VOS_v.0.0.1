"""Module entry point so the pipeline CLI runs as `python3 -m aos_v0.exam.intake`."""

from .pipeline import main

if __name__ == "__main__":
    raise SystemExit(main())