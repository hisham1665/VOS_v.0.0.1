"""Module entry point so the OCR service runs as `python3 -m aos_v0.exam.ocr`."""

from .pipeline import main

if __name__ == "__main__":
    raise SystemExit(main())