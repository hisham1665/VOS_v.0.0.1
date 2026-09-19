"""ASCII review dashboard (plan Phase 11 "Review Interface").

Renders one ``ReviewItem`` as the plan's card and a queue as a compact table.
The card is text-only so it works on any terminal: no web layer is required
for a human to accept / modify / escalate a proposal.
"""

from __future__ import annotations

from typing import Iterable, List, Optional

from .models import ReviewItem, ReviewStats


def _fmt_marks(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:g}"


def _fmt_pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def render_card(item: ReviewItem) -> str:
    """One 'REVIEW REQUIRED' card, mirroring the plan's drawing."""
    student = item.student or {}
    name = student.get("name") or ""
    roll = student.get("roll_no") or item.paper_id
    width = 64
    body = width - 3
    rule = "+" + "-" * width + "+"

    def row(prefix: str, text: List[str]) -> str:
        line = prefix + " ".join(text)
        return "| " + line.ljust(body) + "|"

    banner = "PAPER-LEVEL" if item.paper_level else item.question_id
    lines = [
        rule,
        row("REVIEW REQUIRED ", [banner]),
        rule,
        row("Student: ", [name, f"({roll})"] if name else [roll]),
    ]

    if item.question_text:
        lines.append(row("Question: ", [item.question_text]))

    if item.source:
        lines.append(row("Evidence: ", ["image file", item.source]))
    else:
        lines.append(row("Evidence: ", ["in-memory document"]))
    if item.pages:
        lines.append(row("Pages: ", [",".join(str(p) for p in item.pages)]))

    if item.answer_text:
        snippet = " ".join(item.answer_text.split())
        lines.append(row("Answer: ", [snippet]))
    lines.append(row("OCR conf: ", [_fmt_pct(item.ocr_confidence)]))
    lines.append(
        row("Versions: ", [f"key v{item.answer_key_version}",
                           f"rubric v{item.rubric_version}"])
    )

    for label, verdict in (("Agent 1", item.agent1), ("Agent 2", item.agent2)):
        if not verdict:
            lines.append(row(label + ": ", ["—"]))
            continue
        marks = _fmt_marks(verdict.get("marks"))
        lines.append(
            row(label + ": ",
                [f"{marks} / {verdict.get('max_marks', 0):g}",
                 f"(confidence {_fmt_pct(verdict.get('confidence'))})"])
        )
        reasoning = (verdict.get("reasoning") or "").strip()
        if reasoning:
            lines.append(row("   → ", [" ".join(reasoning.split())]))

    if item.disagreement:
        disputed = ", ".join(item.disputed_concepts) or "unknown concepts"
        lines.append(row("Disagreement: ", [disputed]))
    if item.reconciliation:
        lines.append(row("Reconciled: ", [json_dumps_small(item.reconciliation)]))

    lines.append(row("Reasons: ", item.review_reasons))
    lines.append(
        row("Confidence: ", [_fmt_pct(item.proposed_confidence)])
    )
    lines.append(
        row("Proposed: ",
            [f"{_fmt_marks(item.proposed_marks)} / {item.max_marks:g}"])
    )
    lines.append(row("Final Marks: ", [_fmt_marks(item.final_marks)]))

    if item.status.value == "reviewed" and item.decision:
        decision = item.decision.value.upper()
        reviewer = f"by {item.reviewer or 'reviewer'}"
        lines.append(row("Decision: ", [decision, reviewer]))

    lines.append(
        row("", ["[Accept] [Modify] [Escalate]", "·", item.id])
    )
    lines.append(rule)
    return "\n".join(lines)


def json_dumps_small(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


def render_queue(items: Iterable[ReviewItem]) -> str:
    """Compact table for the review dashboard list view."""
    header = f"{'item':<28}{'status':<10}{'proposed':<10}{'reasons'}"
    rows = [header, "-" * 70]
    for item in items:
        status = item.status.value
        proposed = f"{_fmt_marks(item.proposed_marks)}/{item.max_marks:g}"
        reasons = ", ".join(item.review_reasons) or "paper-level"
        label = item.question_id if not item.paper_level else "*"
        item_id = f"{item.paper_id}:{label}"
        rows.append(
            f"{item_id:<28}{status:<10}{proposed:<10}{reasons}"
        )
    return "\n".join(rows)


def render_stats(stats: ReviewStats) -> str:
    return (
        f"review queue: {stats.total} total, {stats.pending} pending, "
        f"{stats.reviewed} reviewed"
    )