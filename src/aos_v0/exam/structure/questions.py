"""Question detection + question-to-answer mapping (plan Phase 5).

Consumes the Phase-4 `OcrDocument` (blocks with text/bbox per page) and turns it
into a canonical ("Q3" / "Q3(a)") to block mapping while preserving the plan's
hard cases:

  * **out-of-order answers**  -- mapping is keyed by question id, so reading
    order never matters; the label trace lets the parser *flag* the disorder;
  * **continuation answers**  -- the same id across pages naturally merges into
    one entry with a multi-page `pages` list;
  * **missing question number** -- blocks that precede the first label are kept
    separate for review instead of silently attaching to a neighbour;
  * **question-number OCR errors** -- labels that parse to a number absent from
    the configured question set are surfaced, never rewritten;
  * **multiple attempts**     -- the same id answered in two separate stretches
    on one page is flagged (runs are computed over the global reading order).

The parser never rewrites or discards evidence: every ambiguity becomes an
issue on the entry/sheet, and mapping output is deterministic for tests.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from aos_v0.exam.ocr.models import OcrBlock, OcrBlockType, OcrDocument, RegionType

_Q_PREFIX = re.compile(r"(?i)^q(?:uestion)?\s*[.#:\-_\s]*")
_STRIKE_CHARS = frozenset("×✗✘xX/\\_")
_QUESTION_LIKE = re.compile(r"(?i)^q(?:uestion)?\s*#{0,1}\s*\d{0,2}$")
_BARE_LETTER = re.compile(r"^\(\s*([a-zA-Z])\s*\)|^([a-zA-Z])\s*\)|^([a-zA-Z])\s*$")
_LABEL_LEAD = re.compile(
    r"^\s*(?:[Qq](?:uestion)?\s*[.#:\-_ ]*)?\d{1,3}"
    r"(?:\s*\(\s*[a-zA-Z]\s*\)|\s*[a-zA-Z]\s*\)?)?"
    r"\s*[.):\- ]*"
)


def canonical_question_id(num: int, letter: Optional[str] = None) -> str:
    """Normalize a parsed label to the plan's "Q3" / "Q3(a)" form."""
    if letter:
        return f"Q{num}({letter.lower()})"
    return f"Q{num}"


def strip_label_text(text: str) -> str:
    """Remove a leading question-number token, keeping any trailing prose.

    OCR often merges the printed label and the start of the answer into one
    line ("Q1. Define the OSI model"). The numbering itself is a mapping
    artifact, but the rest of the line is evidence and must not be dropped.
    """
    return _LABEL_LEAD.sub("", text, count=1).strip()


def parse_question_label(text: str) -> Optional[str]:
    """Return the canonical question id if `text` is a question label.

    Two accepted forms:

      * explicit prefix: "Q3(a) Explain the OSI model", "Question 2 -", "q 4.",
        "Q5b Restore"  -- trailing prose is allowed;
      * bare numbered:  "3(a)", "3 a)", "3a", "3.", "3)"  -- the number must
        carry a sub-letter, an open/close paren, or a trailing "." / ")". A
        bare integer ("12") is deliberately rejected (prose/list test).

    A "Q3 Explain" label is "Q3", not "Q3(E)" -- only a letter glued to the
    digits ("Q3a") or bracketed ("Q3(a)") carries a sub-part.
    """
    if not text:
        return None
    raw = text.strip()

    # -- bare-number form --------------------------------------------------
    m = re.match(r"^(\d{1,3})(.*)$", raw)
    if m:
        number = int(m.group(1))
        rest = m.group(2).strip()
        letter: Optional[str] = None
        lm = _BARE_LETTER.match(rest)
        if lm:
            letter = next((g for g in lm.groups() if g), None)
            rest = rest[lm.end():].strip()
            if not re.fullmatch(r"[.):\s]*", rest):
                return None
            return canonical_question_id(number, letter)
        if rest and rest[0] in ".):-":
            return canonical_question_id(number, None)
        return None

    # -- q-prefixed form ---------------------------------------------------
    m = _Q_PREFIX.match(raw)
    if m:
        digits = re.match(r"\d{1,3}", raw[m.end():])
        if not digits:
            return None
        number = int(digits.group(0))
        tail = raw[m.end() + digits.end():]
        letter = None
        lm = re.match(r"^\s*\(\s*([a-zA-Z])\s*\)", tail)
        if lm:
            letter = lm.group(1)
        elif tail and not tail[0].isspace() and tail[0].isalpha():
            letter = tail[0]
        return canonical_question_id(number, letter)

    return None


def looks_like_question_attempt(text: str) -> bool:
    """Heuristic for labels that failed to parse (the OCR-error edge case)."""
    return bool(_QUESTION_LIKE.match(text.strip()))


def looks_crossed_out(text: str) -> bool:
    """Detect a struck-through block from its glyph mix (strike characters).

    Strong evidence is a tight run of strike glyphs (>= 3 consecutive
    x / X / / / backslash / underscore / unicode crosses); weaker evidence is
    a block whose overstrike glyphs are dense (>= 30% of the text) with at
    least five alphanumerics. Conservative so normal prose never trips it.
    """
    cleaned = text.strip()
    if not cleaned:
        return False
    if re.search(r"[xX/\\_]{3,}", cleaned):
        return True
    alnum = [c for c in cleaned if c.isalnum()]
    if len(alnum) < 5:
        return False
    strike = sum(1 for c in cleaned if c in _STRIKE_CHARS)
    return strike / max(1, len(cleaned)) >= 0.3


# ---------------------------------------------------------------------------
# Detection / mapping
# ---------------------------------------------------------------------------


class MappedBlock:
    """A block plus the label and reading position it ended up under."""

    __slots__ = ("canonical", "page_no", "block")

    def __init__(self, canonical: str, page_no: int, block: OcrBlock):
        self.canonical = canonical
        self.page_no = page_no
        self.block = block


class AnswerMapping:
    """Result of the detector/mapper: canonical id -> ordered blocks.

    `content_trace` keeps the *global* reading order of every non-label block
    together with the id it was assigned to. That ordering is what reveals
    interleaved stretches (multiple attempts on one page) without needing any
    per-canonicall bookkeeping.
    """

    def __init__(self):
        self.by_id: Dict[str, List[MappedBlock]] = {}
        self.unassigned: List[MappedBlock] = []
        self.label_trace: List[Tuple[str, int]] = []  # (canonical, page_no)
        self.label_blocks: Dict[str, List[OcrBlock]] = {}
        self.content_trace: List[Tuple[str, int]] = []  # (canonical, page_no)

    def add(self, mapped: MappedBlock, *, label: bool = False) -> None:
        if mapped.canonical is None or mapped.canonical == "":
            self.unassigned.append(mapped)
            if not label:
                self.content_trace.append((mapped.canonical, mapped.page_no))
            return
        self.by_id.setdefault(mapped.canonical, []).append(mapped)
        if label:
            self.label_trace.append((mapped.canonical, mapped.page_no))
            self.label_blocks.setdefault(mapped.canonical, []).append(mapped.block)
        else:
            self.content_trace.append((mapped.canonical, mapped.page_no))

    def stretches_per_page(self, canonical: str) -> Dict[int, int]:
        """page_no -> number of maximal same-page stretches of that answer.

        A stretch is a maximal run of consecutive content blocks assigned to the
        same id on the same page in the global reading order. >1 on one page is
        the multiple-attempts signal; the same id spanning several pages is a
        continuation.
        """
        stretches: Dict[int, int] = {}
        current_page: Optional[int] = None
        for hit_canonical, page_no in self.content_trace:
            if hit_canonical == canonical:
                if current_page != page_no:
                    current_page = page_no
                    stretches[page_no] = stretches.get(page_no, 0) + 1
            else:
                current_page = None
        return stretches

    def pages_for(self, canonical: str) -> List[int]:
        return sorted({m.page_no for m in self.by_id.get(canonical, [])})

    def gap_stretches_for(self, canonical: str, page_no: int,
                          gap: int = 140) -> int:
        """Same-page answer stretches split by a vertical gap (`gap` px).

        Two answer boxes for one question on the same page, separated by a
        large empty band, are counted as separate stretches (the
        multiple-attempts signal). Blocks follow reading order within the page.
        """
        blocks = [m.block for m in self.by_id.get(canonical, [])
                  if m.page_no == page_no]
        stretches = 0
        prev_bottom: Optional[int] = None
        for b in blocks:
            if prev_bottom is None or b.bbox[1] - prev_bottom > gap:
                stretches += 1
            prev_bottom = max(prev_bottom or b.bbox[1], b.bbox[3])
        return stretches

    @property
    def detected_ids(self) -> List[str]:
        seen: List[str] = []
        for canonical, _page in self.label_trace:
            if canonical not in seen:
                seen.append(canonical)
        return seen


def _sort_blocks(blocks: List[OcrBlock]) -> List[OcrBlock]:
    return sorted(blocks, key=lambda b: (b.bbox[1], b.bbox[0]))


def _is_header_block(page, block: OcrBlock) -> bool:
    """Header evidence (name/roll/register lines) is not answer content.

    A block is header evidence when the OCR/layout typed it HEADER or when it
    falls inside a HEADER layout region. Such lines supply the student
    identity (Phase 5 identity extractor) and must never become an answer or an
    unlabelled block.
    """
    if block.type == OcrBlockType.HEADER:
        return True
    for region in page.regions:
        if region.region_type == RegionType.HEADER:
            a, b = block.bbox, region.bbox
            if not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]):
                return True
    return False


def map_answers(doc: OcrDocument) -> AnswerMapping:
    """Walk all pages in reading order and attach blocks to question labels."""
    mapping = AnswerMapping()
    current: Optional[str] = None

    for page in sorted(doc.pages, key=lambda p: p.page_no):
        for block in _sort_blocks(page.blocks):
            if _is_header_block(page, block):
                continue
            canonical = parse_question_label(block.text_clean)
            if canonical is not None:
                current = canonical
                mapping.add(MappedBlock(canonical, page.page_no, block), label=True)
            else:
                mapping.add(MappedBlock(current if current is not None else "",
                                        page.page_no, block))
    return mapping