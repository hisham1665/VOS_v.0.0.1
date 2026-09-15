"""Clinical text-extraction helpers shared by the medical capabilities.

Everything here operates on text already extracted by the existing AOS document
infrastructure (pdfplumber, the same library `capabilities/document.py` uses) --
this module adds page-granularity + parsing on top, it does not introduce a new
PDF/document pipeline.

Functions:
  * extract_pdf_pages    -- page-tagged text extraction (reuses pdfplumber).
  * extract_text         -- one-shot text extraction for any supported file.
  * parse_lab_lines      -- deterministic laboratory parser (test/value/unit/
                            reference range/status) used when the NER model is
                            empty or the report's formatting is plain.
  * extract_patient_info -- regex demographics extractor.
  * find_on_page         -- page lookup helper for traceability.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from aos_v0.medical.manifest import LabResult, PatientProfile

# ---------------------------------------------------------------------------
# Document extraction (reuses the existing pdfplumber-based infrastructure)
# ---------------------------------------------------------------------------


def extract_pdf_pages(path: str | Path) -> List[Tuple[int, str]]:
    """Extract per-page text from a PDF: [(page_number, text), ...]."""
    import pdfplumber

    pages: List[Tuple[int, str]] = []
    with pdfplumber.open(path) as pdf:
        for idx, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append((idx, text))
    return pages


def extract_text(path: str | Path) -> str:
    """Extract all text from a PDF (or read a plain text file) using the same
    pdfplumber infrastructure the existing `document` capability uses.

    When pdfplumber cannot open a `.pdf` (corrupt file, fake extension, password
    protection), it falls back to reading the raw bytes as text so downstream
    parsers still see whatever content exists.
    """
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        try:
            pages = extract_pdf_pages(path)
        except Exception:  # noqa: BLE001 -- corrupt/locked pdf
            return path.read_bytes().decode("utf-8", errors="replace")
        return "\n\n".join(f"[page {n}]\n{t}" for n, t in pages)
    # Plain-text documents: read directly.
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (UnicodeDecodeError, OSError):
        return path.read_bytes().decode("latin-1", errors="replace")


def find_on_page(text: str, keyword: str) -> Optional[int]:
    """Return the page number on which `keyword` first appears in page-tagged text."""
    m = re.search(r"\[page (\d+)\](?:(?!\[page \d+\]).)*?" + re.escape(keyword),
                  text, re.DOTALL | re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Deterministic laboratory parser (fallback + structure layer)
# ---------------------------------------------------------------------------

# Number with optional decimal comma, %, or scientific notation -- the value.
_VALUE_RE = re.compile(
    r"(?<![A-Za-z])(\d{1,4}(?:[.,]\d+)?)\s*(mg/dL|mcg/mL|ng/mL|pg/mL|IU/L|U/L|"
    r"mmol/L|\%|g/dL|g/L|mg/mL|µg/mL|units/L|u/L|mmol|mlU/L|mU/L|×10³/µL|"
    r"10\^9/L|10⁹/L|fL|pg|k/µL|mm3|mm³|cells/µL)?"
)

_STATUS_ALIASES = {
    "high": "HIGH",
    "elevated": "HIGH",
    "elev": "HIGH",
    "raised": "HIGH",
    "low": "LOW",
    "decreased": "LOW",
    "critical": "CRITICAL",
    "critically high": "CRITICAL",
    "critically low": "CRITICAL",
    "abnormal": "UNKNOWN",
}
_STATUS_RE = re.compile(
    r"\b(high|elevated|elev|raised|low|decreased|critical|critically high|"
    r"critically low|abnormal|normal)\b", re.IGNORECASE
)

_RANGE_RE = re.compile(
    r"([<>≤≥]?\s*\d{1,4}(?:[.,]\d+)?\s*[-–—to]\s*[<>≤≥]?\s*\d{1,4}(?:[.,]\d+)?"
    r"\s*(?:mg/dL|mg/dl|mmol/L|g/dL|g/L|mcg/mL|ng/mL|pg/mL|IU/L|U/L|\%|mlU/L|"
    r"mU/L|u/L)?)"
)


def _normalize_value(value_str: str) -> Tuple[Optional[float], Optional[str]]:
    """Return (numeric value, unit) if parseable, else (None, unit)."""
    m = re.match(r"\s*([<>≤≥])?\s*([\d.,]+)\s*([A-Za-zµ×%/³⁹^]*)\s*$", value_str)
    if not m:
        return None, None
    _, num, unit = m.groups()
    try:
        return float(num.replace(",", ".")), unit or None
    except ValueError:
        return None, unit or None


def parse_lab_lines(page_text: str, source: str, default_page: Optional[int] = None) -> List[LabResult]:
    """Parse laboratory rows from plain (line-oriented) report text.

    Handles patterns like::

        Glucose          142      mg/dL    70-100 mg/dL     HIGH

    Rules:
      * a line is a lab row when it starts with a test name (letter/word
        tokens) and contains a numeric value;
      * reference range / status are taken from the report when present
        (never invented); status defaults to UNKNOWN without a numeric range;
      * status may also be taken verbatim from the report text.

    This is the deterministic structural layer used alongside the HF NER model:
    NER when available, this parser as the primary structural fallback.
    """
    results: List[LabResult] = []
    page = default_page or 1

    for raw_line in page_text.splitlines():
        line = raw_line.strip()
        if not line or len(line) < 4:
            continue
        value_m = _VALUE_RE.search(line)
        if not value_m:
            continue
        # Test-name lead: strip the leading numeric value/unit to recover name.
        prefix = line[: value_m.start()].strip(" \t|,;:")
        if not prefix or not re.search(r"[A-Za-zÀ-ÿ]{3,}", prefix):
            continue

        value_token = value_m.group(1)
        unit = value_m.group(2) or ""
        remainder = line[value_m.end():]

        ref_range = ""
        range_m = _RANGE_RE.search(remainder)
        if range_m:
            ref_range = " ".join(range_m.group(1).split())

        status = _STATUS_ALIASES.get("normal", "UNKNOWN")
        status_m = _STATUS_RE.search(remainder)
        if status_m:
            key = status_m.group(1).lower()
            status = _STATUS_ALIASES.get(key, "UNKNOWN")
        elif ref_range:
            # Independent numeric check against the documented reference range.
            low_m = re.search(r"([\d.,]+)\s*[-–—to]\s*([\d.,]+)", ref_range)
            if low_m:
                try:
                    low = float(low_m.group(1).replace(",", "."))
                    high = float(low_m.group(2).replace(",", "."))
                    num, _ = _normalize_value(value_token)
                    if num is not None:
                        if num < low:
                            status = "LOW"
                        elif num > high:
                            status = "HIGH"
                        else:
                            status = "NORMAL"
                except ValueError:
                    status = "UNKNOWN"

        results.append(
            LabResult(
                test=" ".join(prefix.split()),
                value=value_token,
                unit=unit,
                reference_range=ref_range,
                status=status,
                source_file=source,
                page=page,
            )
        )
    return results


def merge_statuses(documented: Optional[str], computed: Optional[str]) -> str:
    """Prefer a status the report explicitly documents; fall back to computed."""
    if documented and documented != "UNKNOWN":
        return documented
    return computed or "UNKNOWN"


# ---------------------------------------------------------------------------
# Patient demographics
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(
    r"(?:patient\s*(?:name|id|identifier)\s*[:=]?|name\s*[:=])\s*"
    r"([A-Z][A-Za-zÀ-ÿ'.\- ]+)", re.IGNORECASE
)
_AGE_RE = re.compile(r"(?:age\s*[:=]?)\s*(\d{1,3})\s*(?:years?|yrs?|y|yo)?", re.IGNORECASE)
_DOB_RE = re.compile(r"(?:date of birth|dob|d\.o\.b\.)\s*[:=]?\s*([\d/.\-]{6,10})", re.IGNORECASE)
_SEX_RE = re.compile(r"(?:sex|gender)\s*[:=]?\s*(male|female|m|f)\b", re.IGNORECASE)


def extract_patient_info(text: str, source: str = "") -> PatientProfile:
    """Best-effort demographics extraction. Every field is optional and reflects
    the source text only -- never guessed."""
    profile = PatientProfile()
    name_m = _NAME_RE.search(text or "")
    if name_m:
        profile.name = " ".join(name_m.group(1).split())
    dob_m = _DOB_RE.search(text or "")
    age_m = _AGE_RE.search(text or "")
    if age_m:
        profile.age = age_m.group(1)
    elif dob_m:
        profile.notes = f"DOB captured but age not stated ({dob_m.group(1)})"
    sex_m = _SEX_RE.search(text or "")
    if sex_m:
        raw = sex_m.group(1).lower()
        profile.sex = "F" if raw == "f" or raw.startswith("female") else "M"
    return profile