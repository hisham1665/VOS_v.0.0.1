"""Document extraction capability: reads text content from PDF files."""

from pathlib import Path
from typing import Optional


def run(doc_path: str, instruction: Optional[str] = None) -> str:
    """Extract text from a PDF document.

    Uses pdfplumber for high-fidelity text extraction (handles real PDFs,
    tables, and multi-column layouts better than naive PyPDF2).

    `instruction` is accepted for interface parity with other capabilities but
    extraction is deterministic -- it does not change the parsed output.
    """
    if not Path(doc_path).is_file():
        raise FileNotFoundError(
            f"Document capability requires a valid document file, "
            f"got: '{doc_path}'"
        )

    try:
        import pdfplumber
    except ImportError:
        raise RuntimeError(
            "Document extraction requires 'pdfplumber'. Install it with: "
            "pip install pdfplumber"
        )

    with pdfplumber.open(doc_path) as pdf:
        pages = []
        for page in pdf.pages:
            text = page.extract_text() or ""
            if text.strip():
                pages.append(text)
        content = "\n\n".join(pages).strip()

    if not content:
        return "NO_DATA: PDF contained no extractable text (may be a scanned/image-only document)."
    return content