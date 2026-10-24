"""Native document extraction with stable source references and explicit empty-page gaps."""

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

    path = Path(doc_path)
    if path.suffix.lower() in {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".html"}:
        return f"[SOURCE: {path.name}]\n{path.read_text(encoding='utf-8', errors='replace')}"
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Unsupported document format {path.suffix!r}; use PDF or text/CSV/JSON/YAML")

    try:
        import pdfplumber
    except ImportError:
        raise RuntimeError(
            "Document extraction requires 'pdfplumber'. Install it with: "
            "pip install pdfplumber"
        )

    with pdfplumber.open(doc_path) as pdf:
        pages = []
        for number, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(f"[SOURCE: page={number}]\n{text}")
            else:
                pages.append(f"[SOURCE: page={number}]\n[NO_NATIVE_TEXT: page requires OCR/vision]")
        content = "\n\n".join(pages).strip()

    if not content or all("[NO_NATIVE_TEXT:" in page for page in pages):
        return "NO_DATA: PDF contained no extractable text (may be a scanned/image-only document)."
    return content
