"""The chat engine: turns a message into analysis runs and grounded answers.

UI-agnostic on purpose. The Textual TUI, a plain REPL, or a test all drive the
same :class:`ChatEngine`; the engine reports everything it does through one
``emit`` path, which also writes the session transcript and terminal log --
so "everything shown is logged" holds by construction rather than by every
UI remembering to log.

Flow for one message::

    raw text --parse--> question + attached files
        files?  -> copy into session/uploads -> analyze the whole session
                   folder (pipeline.analyze_study; cached per file hash, so
                   re-analysis only pays for new files) -> index results
        question? -> assemble layered context (chat.memory) -> LLM -> answer
                   -> index the turn -> fold old turns into the summary
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from chat.attachments import parse_message
from chat.memory import Chunk, MemoryIndex, clip, split_text, warm_up
from chat.session import AnalysisRun, Session, SessionStore
from config import CHAT
from core.logging_utils import add_handler, get_logger, remove_handler
from models import DISCLAIMER

_log = get_logger("chat")

Emit = Callable[[dict], None]
Analyzer = Callable[..., tuple]
LLM = Callable[..., tuple[str, str]]

_SYSTEM = (
    "You are Medical AOS, a clinical decision-support assistant talking with a "
    "clinician inside one patient session. You can see a brief of the session "
    "(uploaded files, the latest automated analysis), a summary of earlier "
    "conversation, the most recent turns, and evidence passages retrieved from "
    "the session's own documents and image reads.\n\n"
    "Rules:\n"
    "- Ground every patient-specific statement in the session material and cite "
    "it inline as [source] using the exact source labels given.\n"
    "- If the material does not contain the answer, say so plainly; you may add "
    "general medical knowledge only when clearly labelled as general knowledge.\n"
    "- Automated image findings are preliminary model output, not a radiologist "
    "read; keep that distinction visible when it matters.\n"
    "- Never invent values, dates, measurements or files.\n"
    "- Be concise. Use short markdown: bullets, bold for key values, a small "
    "table when comparing. No preamble."
)

_SUMMARY_SYSTEM = (
    "You maintain the running memory of a clinical chat. Merge the earlier "
    "summary and the new turns into one updated summary of at most 250 words. "
    "Keep: patient facts, files discussed, findings and values cited, the "
    "clinician's questions and conclusions, open questions. Drop pleasantries."
)

HELP = """\
**Messages**
- Type a question, or attach files with `@path`, `@"path with spaces"`, or by dragging files/folders into the terminal. Images, DICOM, PDF, TXT/MD, CSV, JSON, DOCX, XLSX.
- Attaching files analyses the whole session folder; your text becomes the referring question.

**Commands**
| Command | What it does |
|---|---|
| `/attach <paths>` | add files or folders without asking anything |
| `/analyze` | re-run the analysis on everything uploaded |
| `/history <text>` | set the clinical history used by every analysis |
| `/files` | list uploaded files |
| `/report [n]` | show the latest (or run *n*) report summary |
| `/memory` | show what the assistant remembers (summary + index size) |
| `/literature on\\|off` | toggle the web literature lookup during analysis |
| `/sessions` | list saved sessions |
| `/resume [id\\|name]` | reopen a session (picker if no argument) |
| `/new [name]` | start a new session |
| `/rename <name>` | rename this session (folder too) |
| `/clear` | clear the screen (history is kept) |
| `/quit` | exit |
"""


@dataclass
class EngineResult:
    """What the UI needs to do after a message is handled."""

    action: str = ""  # "" | resume | new | quit | clear
    argument: str = ""


class _LogBridge(logging.Handler):
    """Forward pipeline progress lines to the UI as ``progress`` events."""

    def __init__(self, emit_progress: Callable[[str], None]):
        super().__init__(level=logging.INFO)
        self.emit_progress = emit_progress

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401 - logging API
        message = record.getMessage()
        if record.name.endswith(("pipeline", "executor")) and (
            "stage" in message or message.startswith("wave") or "complete" in message
        ):
            self.emit_progress(message)


class ChatEngine:
    def __init__(
        self,
        session: Session,
        store: SessionStore,
        *,
        on_event: Emit | None = None,
        analyzer: Analyzer | None = None,
        llm: LLM | None = None,
    ):
        self.session = session
        self.store = store
        self.on_event = on_event or (lambda _e: None)
        self._analyzer = analyzer
        self._llm = llm
        self.memory = MemoryIndex(session.memory_dir)
        warm_up()  # load the dense embedder in the background, never inline
        self._busy = threading.Lock()
        self._file_handler: logging.Handler | None = None
        self._attach_session_log()

    # ------------------------------------------------------------ plumbing
    def emit(self, kind: str, text: str = "", **data: Any) -> dict:
        event = self.session.log_event(kind, text, **data)
        try:
            self.on_event(event)
        except Exception:  # noqa: BLE001 - a UI bug must not lose the log
            _log.exception("UI event handler failed")
        return event

    def _attach_session_log(self) -> None:
        self.session.logs_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(self.session.pipeline_log_path, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        add_handler(handler)
        self._file_handler = handler

    def close(self) -> None:
        if self._file_handler is not None:
            remove_handler(self._file_handler)
            self._file_handler.close()
            self._file_handler = None

    @property
    def busy(self) -> bool:
        return self._busy.locked()

    # -------------------------------------------------------------- entry
    def handle(self, raw: str) -> EngineResult:
        raw = raw.strip()
        if not raw:
            return EngineResult()
        if raw.startswith("/"):
            return self._command(raw)
        with self._busy:
            self._message(raw)
        return EngineResult()

    # ------------------------------------------------------------ messages
    def _message(self, raw: str, *, analyze_only: bool = False) -> None:
        parsed = parse_message(raw, cwd=Path.cwd())
        names = [str(p) for p in parsed.files]
        self.emit("user", parsed.text or ("(attached files)" if names else raw),
                  attachments=[Path(n).name for n in names], raw=raw)
        for problem in parsed.rejected:
            self.emit("error", f"Could not attach {problem}.")

        if parsed.files:
            added = self._add_files(parsed.files)
            if added or not self.session.meta.analyses:
                self._analyze(question=parsed.text or None)
            else:
                self.emit("system", "All attached files were already in this session; nothing new to analyse.")

        if parsed.text and not analyze_only:
            self._answer(parsed.text)
        elif parsed.files and not parsed.text:
            pass  # the analysis summary is the reply
        elif not parsed.files and not parsed.text:
            self.emit("error", "Nothing to do: the message only contained paths that could not be attached.")

    def _add_files(self, paths: list[Path]) -> int:
        added, duplicate = [], []
        for path in paths:
            try:
                record, is_new = self.session.add_file(path)
            except OSError as exc:
                self.emit("error", f"Could not copy {path.name}: {exc}")
                continue
            (added if is_new else duplicate).append(record.name)
        if added:
            self.emit("system", f"Attached {len(added)} file(s): " + ", ".join(added),
                      files=added)
        if duplicate:
            self.emit("system", "Already in session (skipped): " + ", ".join(duplicate))
        return len(added)

    # ------------------------------------------------------------ analysis
    def _analyze(self, question: str | None = None) -> None:
        if not self.session.meta.files:
            self.emit("error", "No files in this session yet. Attach some with @path first.")
            return
        run = self.session.next_run_number()
        output_dir = self.session.reports / f"run-{run:03d}"
        literature = bool(self.session.meta.settings.get("literature", CHAT.literature))
        self.emit("progress", f"Analysing {len(self.session.meta.files)} file(s) (run {run})...")

        bridge = _LogBridge(emit_progress=lambda m: self.emit("progress", m))
        add_handler(bridge)
        started = time.perf_counter()
        try:
            report, study, graph = self._run_analyzer(
                self.session.uploads,
                clinical_history=self.session.meta.clinical_history or None,
                referring_question=question,
                with_literature=literature,
                output_dir=output_dir,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self.emit("error", f"Analysis failed: {type(exc).__name__}: {exc}")
            return
        finally:
            remove_handler(bridge)

        md = sorted(output_dir.glob("*.md"))
        js = sorted(output_dir.glob("*.json"))
        record = AnalysisRun(
            run=run,
            at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            report_md=str(md[-1].relative_to(self.session.folder)) if md else "",
            report_json=str(js[-1].relative_to(self.session.folder)) if js else "",
            status=report.status.overall_severity.value,
            triage=report.status.triage,
            files=[f.name for f in self.session.meta.files],
            degraded=report.degraded,
        )
        self.session.record_analysis(record)
        indexed = self._index_analysis(report, study, graph, run)
        _write_brief(self.session, report, study)
        self.emit(
            "analysis",
            render_analysis(report, record, time.perf_counter() - started),
            run=run, status=record.status, triage=record.triage,
            report=record.report_md, indexed=indexed,
        )

    def _run_analyzer(self, folder: Path, **kwargs):
        if self._analyzer is not None:
            return self._analyzer(folder, **kwargs)
        from pipeline import analyze_study

        return analyze_study(folder, **kwargs)

    def _index_analysis(self, report, study, graph, run: int) -> int:
        chunks: list[Chunk] = []
        status = report.status
        chunks.append(Chunk(
            id=f"r{run}-report", kind="report", source=f"analysis run {run}", run=run,
            text=(
                f"Overall status {status.overall_severity.value}, triage {status.triage}. "
                f"{status.summary}\nImpression: {report.impression}\n"
                f"Red flags: {'; '.join(status.red_flags) or 'none'}\n"
                f"Recommended: {'; '.join(status.recommended_actions) or 'none'}"
            ),
        ))
        for i, finding in enumerate(report.findings):
            where = ", ".join(finding.image_ids + finding.source_refs) or "study"
            chunks.append(Chunk(
                id=f"r{run}-finding-{i}", kind="finding", run=run,
                source=where,
                text=(f"Finding: {finding.label} ({finding.severity.value}"
                      f"{f', confidence {finding.confidence:.0%}' if finding.confidence else ''}). "
                      f"{finding.description}"),
            ))
        for image_id, entry in (report.screening or {}).items():
            candidates = ", ".join(f"{c['condition']} {c['score']:.0%}" for c in entry.get("candidates", []))
            checks = "; ".join(
                f"{c['target']} {c['verdict']}" + (f" ({c['probability']:.0%})" if "probability" in c else "")
                for c in entry.get("confirmations", [])
            )
            chunks.append(Chunk(
                id=f"r{run}-screen-{image_id}", kind="screening", source=image_id, run=run,
                text=(f"Screening of {image_id}: {entry.get('modality')} / {entry.get('region')}; "
                      f"{entry.get('status')} {entry.get('reason', '')}. Candidates: {candidates or 'none'}. "
                      f"Specialist checks: {checks or 'none'}."),
            ))
        for node in graph.nodes:
            for image_id, text in (node.artifacts.get("reads") or {}).items():
                for j, piece in enumerate(split_text(str(text))):
                    chunks.append(Chunk(id=f"r{run}-read-{image_id}-{j}", kind="image_read",
                                        source=image_id, run=run, text=piece))
        for ref, text in (report.source_reads or {}).items():
            for j, piece in enumerate(split_text(str(text))):
                chunks.append(Chunk(id=f"r{run}-docread-{ref}-{j}", kind="document_read",
                                    source=ref, run=run, text=piece))
        added = self.memory.replace_analysis(chunks)

        # Raw document text is content-addressed, so it survives re-analysis.
        raw: list[Chunk] = []
        for document in study.documents:
            for page in document.pages:
                for j, piece in enumerate(split_text(page.text or "")):
                    raw.append(Chunk(
                        id=f"doc-{document.content_hash[:12]}-{page.page_number}-{j}",
                        kind="document", source=f"{document.document_id}#page={page.page_number}",
                        text=piece,
                    ))
        return added + self.memory.add(raw)

    # -------------------------------------------------------------- answer
    def _answer(self, question: str) -> None:
        prompt, sources = self.build_context(question)
        self.emit("progress", "Thinking...")
        try:
            text, model = self._call_llm(prompt, system=_SYSTEM, max_tokens=CHAT.answer_max_tokens)
        except Exception as exc:  # noqa: BLE001
            self.emit("error", f"Could not generate an answer: {type(exc).__name__}: {exc}")
            return
        self.emit("assistant", text.strip(), model=model, sources=sources)
        turn = self.session.meta.turns
        self.memory.add([Chunk(id=f"turn-{turn}", kind="turn", source=f"conversation turn {turn + 1}",
                               text=f"Clinician asked: {question}\nAssistant answered: {text.strip()}")])
        self.session.meta.turns = turn + 1
        self.session.save()
        self._maybe_fold_history()

    def _call_llm(self, prompt: str, **kwargs) -> tuple[str, str]:
        if self._llm is not None:
            return self._llm(prompt, **kwargs)
        from capabilities.hf_backend import chat_text

        return chat_text(prompt, temperature=0.2, **kwargs)

    def build_context(self, question: str) -> tuple[str, list[str]]:
        """Assemble the layered, budgeted prompt. Public for tests and /context."""
        meta = self.session.meta
        turns = self.session.conversation()
        # The current question was already logged as the last user turn.
        if turns and turns[-1]["role"] == "user" and turns[-1]["text"] == question:
            turns = turns[:-1]
        live = turns[meta.summarized_turns:]

        recent: list[str] = []
        answers_in_window = 0
        used = 0
        for turn in reversed(live):
            line = f"{'Clinician' if turn['role'] == 'user' else 'Assistant'}: {turn['text']}"
            if used + len(line) > CHAT.recent_chars:
                break
            recent.insert(0, line)
            used += len(line)
            answers_in_window += turn["role"] == "assistant"
        # Turns already shown verbatim need not be retrieved again.
        recent_turn_ids = {f"turn-{i}" for i in range(meta.turns - answers_in_window, meta.turns)}

        # Source evidence: ranked hits first, then -- budget permitting -- the
        # rest of the corpus in its original order. A small session therefore
        # has *all* of its material in context and cannot suffer a retrieval
        # miss; a large one degrades gracefully to top-k.
        ranked = self.memory.search(question, exclude_kinds={"turn"})
        ranked_ids = {c.id for c in ranked}
        rest = [c for c in self.memory.chunks if c.kind != "turn" and c.id not in ranked_ids]
        evidence, sources, used = [], [], 0
        for chunk in ranked + rest:
            block = f"[{chunk.source}] ({chunk.kind})\n{chunk.text}"
            if used + len(block) > CHAT.retrieved_chars:
                if chunk.id in ranked_ids:
                    block = clip(block, CHAT.retrieved_chars - used)
                    if len(block) < 200:
                        break
                else:
                    continue
            evidence.append(block)
            if chunk.source not in sources:
                sources.append(chunk.source)
            used += len(block)

        # Past answers are retrieved separately and flagged: an earlier wrong
        # answer must not read as evidence ("no haemoglobin in the report").
        past = [c for c in self.memory.search(question, k=2, exclude_kinds=self.memory.ANALYSIS_KINDS | {"document"})
                if c.kind == "turn" and c.id not in recent_turn_ids]
        earlier = "\n\n".join(f"[{c.source}]\n{clip(c.text, 1200)}" for c in past)

        sections = [
            "## Session brief\n" + clip(session_brief(self.session), CHAT.brief_chars),
            "## Earlier conversation (summary)\n" + (clip(meta.summary, CHAT.summary_chars) or "(none)"),
            "## Evidence from this session's files and analysis\n" + ("\n\n".join(evidence) or "(no material uploaded yet)"),
            "## Possibly related earlier exchanges (may contain mistakes; the evidence above wins)\n"
            + (earlier or "(none)"),
            "## Recent conversation\n" + ("\n".join(recent) or "(this is the first question)"),
            "## Current question\n" + question,
        ]
        return "\n\n".join(sections), sources

    def _maybe_fold_history(self) -> None:
        """Summary-buffer memory: fold old turns once they exceed the budget."""
        meta = self.session.meta
        turns = self.session.conversation()
        live = turns[meta.summarized_turns:]
        if sum(len(t["text"]) for t in live) <= CHAT.recent_chars or len(live) <= 4:
            return
        fold = live[:-4]  # always keep the last two exchanges verbatim
        transcript = "\n".join(
            f"{'Clinician' if t['role'] == 'user' else 'Assistant'}: {clip(t['text'], 1500)}"
            for t in fold
        )
        try:
            summary, _ = self._call_llm(
                f"EARLIER SUMMARY:\n{meta.summary or '(none)'}\n\nNEW TURNS:\n{transcript}",
                system=_SUMMARY_SYSTEM, max_tokens=500,
            )
        except Exception as exc:  # noqa: BLE001 - keep verbatim history instead
            _log.warning("could not fold history: %s", exc)
            return
        meta.summary = summary.strip()
        meta.summarized_turns += len(fold)
        self.session.save()
        self.emit("memory", f"Folded {len(fold)} earlier message(s) into the session summary.")

    # ------------------------------------------------------------ commands
    def _command(self, raw: str) -> EngineResult:
        name, _, arg = raw[1:].partition(" ")
        name, arg = name.lower(), arg.strip()
        self.session.log_event("command", raw)

        if name in ("quit", "exit", "q"):
            return EngineResult("quit")
        if name == "clear":
            return EngineResult("clear")
        if name == "new":
            return EngineResult("new", arg)
        if name == "resume":
            return EngineResult("resume", arg)
        if name == "help":
            self.emit("system", HELP)
        elif name == "attach":
            if not arg:
                self.emit("error", "Usage: /attach <file or folder> ...")
            else:
                refs = " ".join(f'@"{token.strip(chr(34) + chr(39))}"' for token in _split_args(arg))
                with self._busy:
                    self._message(refs, analyze_only=True)
        elif name == "analyze":
            with self._busy:
                self._analyze(question=arg or None)
        elif name == "history":
            if arg:
                self.session.meta.clinical_history = arg
                self.session.save()
                self.emit("system", f"Clinical history set: {arg}\nRun `/analyze` to apply it to the analysis.")
            else:
                self.emit("system", f"Clinical history: {self.session.meta.clinical_history or '(not set)'}")
        elif name == "files":
            self.emit("system", render_files(self.session))
        elif name == "report":
            self.emit("system", self._report_text(arg))
        elif name == "memory":
            meta = self.session.meta
            kinds: dict[str, int] = {}
            for chunk in self.memory.chunks:
                kinds[chunk.kind] = kinds.get(chunk.kind, 0) + 1
            self.emit("system", (
                f"**Summary** ({meta.summarized_turns} folded message(s)):\n\n{meta.summary or '_none yet_'}\n\n"
                f"**Indexed chunks:** " + (", ".join(f"{k} {v}" for k, v in sorted(kinds.items())) or "none")
            ))
        elif name == "literature":
            value = arg.lower() in ("on", "1", "true", "yes")
            if arg.lower() not in ("on", "off", "1", "0", "true", "false", "yes", "no"):
                self.emit("system", f"Literature lookup is {'on' if self.session.meta.settings.get('literature', CHAT.literature) else 'off'}. Use `/literature on|off`.")
            else:
                self.session.meta.settings["literature"] = value
                self.session.save()
                self.emit("system", f"Literature lookup {'enabled' if value else 'disabled'} for this session.")
        elif name == "sessions":
            self.emit("system", render_sessions(self.store, current=self.session))
        elif name == "rename":
            if not arg:
                self.emit("error", "Usage: /rename <new name>")
            else:
                self.close()
                self.store.rename(self.session, arg)
                self.memory = MemoryIndex(self.session.memory_dir)
                self._attach_session_log()
                self.emit("system", f"Session renamed to **{arg}** (folder `{self.session.folder.name}`).")
        else:
            self.emit("error", f"Unknown command `/{name}`. Type `/help`.")
        return EngineResult()

    def _report_text(self, arg: str) -> str:
        analyses = self.session.meta.analyses
        if not analyses:
            return "No analysis has run in this session yet."
        run = analyses[-1]
        if arg.isdigit():
            matches = [a for a in analyses if a.run == int(arg)]
            if not matches:
                return f"No run {arg}. Runs: {', '.join(str(a.run) for a in analyses)}."
            run = matches[0]
        path = self.session.folder / run.report_md
        if not path.exists():
            return f"Report file missing: `{run.report_md}`"
        return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Rendering helpers (markdown, shared by the TUI and the logs)
# --------------------------------------------------------------------------
_BADGE = {"critical": "🔴 CRITICAL", "high": "🟠 HIGH", "moderate": "🟡 MODERATE",
          "low": "🔵 LOW", "normal": "🟢 NORMAL", "indeterminate": "⚪ INDETERMINATE"}


def render_analysis(report, record: AnalysisRun, seconds: float) -> str:
    status = report.status
    lines = [
        f"### Analysis run {record.run} — {_BADGE.get(status.overall_severity.value, status.overall_severity.value)} · triage **{status.triage.upper()}**",
        "",
        status.summary or "",
    ]
    if report.degraded:
        lines += ["", "> ⚠️ Degraded run — some stages failed; see `/report` warnings."]
    if report.findings:
        lines += ["", "| Severity | Finding | Seen on | Confidence |", "|---|---|---|---|"]
        for f in report.findings[:12]:
            where = ", ".join(f.image_ids + f.source_refs) or "-"
            conf = f"{f.confidence:.0%}" if f.confidence is not None else "-"
            lines.append(f"| {_BADGE.get(f.severity.value, f.severity.value)} | {f.label} | {where} | {conf} |")
    if report.impression:
        lines += ["", f"**Impression.** {report.impression}"]
    if status.recommended_actions:
        lines += ["", "**Next steps**"] + [f"{i}. {a}" for i, a in enumerate(status.recommended_actions, 1)]
    unscreened = [i for i, e in (report.screening or {}).items() if e.get("status") != "screened"]
    if unscreened:
        lines += ["", f"_Not checked by any supervised model: {', '.join(unscreened)}._"]
    lines += ["", f"_Report: `{record.report_md}` · {seconds:.0f}s · {DISCLAIMER.split('.')[0]}._"]
    return "\n".join(lines)


def render_files(session: Session) -> str:
    if not session.meta.files:
        return "No files uploaded yet."
    lines = ["| # | File | Type | Size | Added |", "|---|---|---|---:|---|"]
    for i, f in enumerate(session.meta.files, 1):
        lines.append(f"| {i} | `{f.name}` | {f.kind} | {f.size / 1024:.0f} KB | {f.added_at[:16].replace('T', ' ')} |")
    return "\n".join(lines)


def render_sessions(store: SessionStore, current: Session | None = None) -> str:
    sessions = store.list()
    if not sessions:
        return "No saved sessions."
    lines = ["| | ID | Name | Files | Runs | Updated |", "|---|---|---|---:|---:|---|"]
    for s in sessions[:30]:
        mark = "▶" if current is not None and s.id == current.id else ""
        lines.append(f"| {mark} | `{s.id}` | {s.name} | {len(s.meta.files)} | {len(s.meta.analyses)} | {s.meta.updated_at[:16].replace('T', ' ')} |")
    lines.append("\nResume with `/resume <id or name>`.")
    return "\n".join(lines)


def session_brief(session: Session) -> str:
    """The always-included structured state (layer 1)."""
    meta = session.meta
    brief_path = session.memory_dir / "brief.md"
    parts = [f"Session '{meta.name}' (id {meta.id}). Clinical history: {meta.clinical_history or 'not provided'}."]
    if meta.files:
        parts.append("Uploaded files: " + ", ".join(f"{f.name} ({f.kind})" for f in meta.files) + ".")
    else:
        parts.append("No files uploaded yet.")
    if brief_path.exists():
        parts.append(brief_path.read_text(encoding="utf-8"))
    elif meta.files:
        parts.append("Files have not been analysed yet.")
    return "\n".join(parts)


def _write_brief(session: Session, report, study) -> None:
    status = report.status
    lines = [f"Latest analysis (run {len(session.meta.analyses)}): status {status.overall_severity.value}, "
             f"triage {status.triage}. {status.summary}"]
    if study.patient.age or study.patient.sex:
        lines.append(f"Patient: age {study.patient.age or '?'}, sex {study.patient.sex or '?'}.")
    for image in study.images:
        lines.append(f"- image {image.image_id}: {image.modality.value}"
                     f"{', ' + image.body_part if image.body_part else ''}")
    for ref, kind in (report.document_types or {}).items():
        lines.append(f"- document {ref}: {kind}")
    if report.findings:
        lines.append("Findings:")
        for f in report.findings:
            lines.append(f"- [{f.severity.value}] {f.label}: {f.description} "
                         f"(sources: {', '.join(f.image_ids + f.source_refs) or 'study'})")
    if report.impression:
        lines.append(f"Impression: {report.impression}")
    if status.recommended_actions:
        lines.append("Recommended: " + "; ".join(status.recommended_actions))
    (session.memory_dir / "brief.md").write_text("\n".join(lines), encoding="utf-8")


def _split_args(arg: str) -> list[str]:
    import shlex

    try:
        return shlex.split(arg, posix=False)
    except ValueError:
        return arg.split()
