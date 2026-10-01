"""Chat sessions, memory and the engine -- no models, no network.

The analyzer and the LLM are stubbed; what is under test is persistence,
attachment handling, context assembly (does a follow-up question see what was
uploaded earlier?), resume, and logging.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from models import (
    DocumentPage, Finding, Graph, Modality, Node, PatientStatus,
    RadiologyReport, Severity, Study, StudyDocument, StudyImage,
)


@pytest.fixture(autouse=True)
def no_dense(monkeypatch):
    """Keep tests offline: BM25 only."""
    monkeypatch.setattr("chat.memory._embedder", lambda: None)


@pytest.fixture
def store(tmp_path):
    from chat.session import SessionStore

    return SessionStore(tmp_path / "sessions")


@pytest.fixture
def files(tmp_path):
    from PIL import Image

    folder = tmp_path / "incoming"
    folder.mkdir()
    Image.new("L", (32, 32), 120).save(folder / "chest xray.png")
    (folder / "labs.txt").write_text("CRP 92 mg/L (high). WBC 15.9 x10^9/L.", encoding="utf-8")
    (folder / "notes.exe").write_bytes(b"MZ")
    return folder


def fake_analyzer(calls: list):
    def analyze(folder, *, clinical_history=None, referring_question=None,
                with_literature=False, output_dir=None):
        calls.append({"folder": Path(folder), "question": referring_question,
                      "history": clinical_history, "files": sorted(p.name for p in Path(folder).iterdir())})
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "report.md").write_text("# Report\nRight lower lobe consolidation.", encoding="utf-8")
        (output_dir / "report.json").write_text("{}", encoding="utf-8")
        images = [StudyImage(image_id=p.name, source_path=str(p), content_hash=p.name,
                             modality=Modality.XRAY, body_part="chest")
                  for p in Path(folder).glob("*.png")]
        documents = [StudyDocument(
            document_id=p.name, source_path=str(p), content_hash="h" + p.name, format="txt",
            pages=[DocumentPage(page_number=1, text=p.read_text(encoding="utf-8"))], page_count=1)
            for p in Path(folder).glob("*.txt")]
        study = Study(study_id="s", source_dir=str(folder), images=images, documents=documents)
        report = RadiologyReport(
            study_id="s",
            findings=[Finding(label="Right lower lobe consolidation", description="Airspace opacity RLL.",
                              severity=Severity.HIGH, confidence=0.8, image_ids=[i.image_id for i in images])],
            impression="Findings suggest right lower lobe pneumonia.",
            status=PatientStatus(overall_severity=Severity.HIGH, triage="urgent",
                                 summary="Likely pneumonia.", recommended_actions=["Start antibiotics per protocol"]),
            source_reads={f"{d.document_id}#page=1": "Lab report: CRP 92 mg/L high." for d in documents},
        )
        graph = Graph(job="j", nodes=[Node(id="read0", description="r", capability="vision", status="done",
                                           artifacts={"reads": {i.image_id: "PA chest film: dense RLL consolidation." for i in images}})])
        return report, study, graph
    return analyze


class FakeLLM:
    def __init__(self):
        self.prompts: list[tuple[str, str]] = []

    def __call__(self, prompt, *, system=None, max_tokens=None):
        self.prompts.append((system or "", prompt))
        if "running memory" in (system or ""):
            return "Summary: clinician reviewed RLL consolidation and CRP 92.", "fake"
        return f"Answer #{len(self.prompts)} [labs.txt#page=1]", "fake-llm"


@pytest.fixture
def engine_factory(store):
    made = []

    def build(session=None, *, llm=None, calls=None, events=None):
        from chat.engine import ChatEngine

        session = session or store.create("patient-001")
        engine = ChatEngine(session, store, on_event=(events.append if events is not None else None),
                            analyzer=fake_analyzer(calls if calls is not None else []),
                            llm=llm or FakeLLM())
        made.append(engine)
        return engine

    yield build
    for engine in made:
        engine.close()


# --------------------------------------------------------------------------
# Session store
# --------------------------------------------------------------------------
class TestSessionStore:
    def test_folder_is_named_after_session_and_id(self, store):
        session = store.create("Patient 001")
        assert session.folder.name == f"patient-001_{session.id}"
        for sub in ("uploads", "reports", "memory", "logs"):
            assert (session.folder / sub).is_dir()

    def test_terminal_log_is_headed_with_id_and_name(self, store):
        session = store.create("patient-001")
        session.log_event("user", "hello")
        log = session.terminal_log_path.read_text(encoding="utf-8")
        assert f"session id:   {session.id}" in log
        assert "session name: patient-001" in log
        assert f"[{session.id}] USER" in log and "hello" in log

    def test_find_by_id_prefix_and_name(self, store):
        a = store.create("alpha case")
        store.create("beta case")
        assert store.find(a.id).id == a.id
        assert store.find(a.id[:4]).id == a.id
        assert store.find("alpha").id == a.id
        assert store.find("case") is None  # ambiguous

    def test_files_are_deduplicated_by_content(self, store, files):
        session = store.create("x")
        first, new1 = session.add_file(files / "labs.txt")
        again, new2 = session.add_file(files / "labs.txt")
        assert new1 and not new2 and first.name == again.name
        assert (session.uploads / "labs.txt").exists()

    def test_rename_moves_folder_and_keeps_logging(self, store):
        session = store.create("old")
        store.rename(session, "new name")
        assert session.folder.name == f"new-name_{session.id}"
        session.log_event("system", "after rename")
        assert "after rename" in session.terminal_log_path.read_text(encoding="utf-8")

    def test_torn_transcript_line_is_skipped(self, store):
        session = store.create("x")
        session.log_event("user", "ok")
        with open(session.transcript_path, "a", encoding="utf-8") as handle:
            handle.write('{"kind": "user", "te')
        assert [e["text"] for e in session.events()] == ["ok"]


# --------------------------------------------------------------------------
# Attachments
# --------------------------------------------------------------------------
class TestAttachments:
    def test_quoted_at_and_bare_paths(self, files):
        from chat.attachments import parse_message

        png = files / "chest xray.png"
        txt = files / "labs.txt"
        parsed = parse_message(f'compare @"{png}" with {txt} please')
        assert parsed.files == [png.resolve(), txt.resolve()]
        assert parsed.text == "compare with please"

    def test_dragged_quoted_path_and_folder_expansion(self, files):
        from chat.attachments import parse_message

        parsed = parse_message(f'"{files}" what do you see?')
        assert {p.name for p in parsed.files} == {"chest xray.png", "labs.txt"}
        assert parsed.text == "what do you see?"

    def test_missing_and_unsupported_are_reported(self, files):
        from chat.attachments import parse_message

        parsed = parse_message(f"@nope/missing.pdf @{files / 'notes.exe'} hi")
        assert parsed.files == []
        assert any("not found" in r for r in parsed.rejected)
        assert any("unsupported" in r for r in parsed.rejected)

    def test_quoted_phrase_that_is_not_a_path_is_kept(self):
        from chat.attachments import parse_message

        parsed = parse_message('what does "ground glass" mean')
        assert parsed.text == 'what does "ground glass" mean'


# --------------------------------------------------------------------------
# Memory
# --------------------------------------------------------------------------
class TestMemory:
    def test_bm25_finds_exact_terms_and_persists(self, tmp_path):
        from chat.memory import Chunk, MemoryIndex

        index = MemoryIndex(tmp_path / "m")
        index.add([
            Chunk(id="a", kind="document", source="labs.pdf#page=1", text="CRP 92 mg/L, WBC 15.9"),
            Chunk(id="b", kind="image_read", source="cxr.png", text="Right lower lobe consolidation"),
        ])
        assert index.search("what was the CRP")[0].id == "a"
        reloaded = MemoryIndex(tmp_path / "m")
        assert reloaded.search("consolidation")[0].id == "b"

    def test_new_analysis_replaces_old_reads_but_keeps_documents(self, tmp_path):
        from chat.memory import Chunk, MemoryIndex

        index = MemoryIndex(tmp_path / "m")
        index.add([Chunk(id="doc", kind="document", source="d", text="raw page")])
        index.replace_analysis([Chunk(id="r1", kind="finding", source="x", text="old finding", run=1)])
        index.replace_analysis([Chunk(id="r2", kind="finding", source="x", text="new finding", run=2)])
        assert {c.id for c in index.chunks} == {"doc", "r2"}

    def test_rrf_rewards_agreement(self):
        from chat.memory import _rrf

        assert _rrf([["a", "b", "c"], ["b", "a"]])[:2] in (["a", "b"], ["b", "a"])
        assert _rrf([["x", "y"], ["y", "z"]])[0] == "y"

    def test_split_text_bounds_chunk_size(self):
        from chat.memory import split_text

        pieces = split_text("word " * 2000, size=500, overlap=50)
        assert all(len(p) <= 560 for p in pieces) and len(pieces) > 5


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------
class TestEngine:
    def test_attach_analyse_and_answer(self, engine_factory, files):
        events, calls, llm = [], [], FakeLLM()
        engine = engine_factory(events=events, calls=calls, llm=llm)
        engine.handle(f'@"{files / "chest xray.png"}" @{files / "labs.txt"} is this pneumonia?')

        kinds = [e["kind"] for e in events]
        assert kinds[0] == "user" and "analysis" in kinds and kinds[-1] == "assistant"
        assert calls[0]["question"] == "is this pneumonia?"
        assert calls[0]["files"] == ["chest xray.png", "labs.txt"]
        run = engine.session.meta.analyses[0]
        assert (engine.session.folder / run.report_md).exists()
        assert run.status == "high"

    def test_follow_up_question_sees_earlier_uploads(self, engine_factory, files):
        llm = FakeLLM()
        engine = engine_factory(llm=llm)
        engine.handle(f"@{files / 'labs.txt'} @\"{files / 'chest xray.png'}\"")
        engine.handle("what was the CRP value?")

        _system, prompt = llm.prompts[-1]
        assert "## Session brief" in prompt
        assert "labs.txt" in prompt  # file manifest
        assert "CRP 92" in prompt  # retrieved document text
        assert "Right lower lobe consolidation" in prompt  # brief findings

    def test_recent_turns_are_in_context(self, engine_factory):
        llm = FakeLLM()
        engine = engine_factory(llm=llm)
        engine.handle("first question about sodium")
        engine.handle("and the second one?")
        prompt = llm.prompts[-1][1]
        assert "Clinician: first question about sodium" in prompt
        assert "Assistant: Answer #1" in prompt

    def test_resume_restores_memory_and_conversation(self, engine_factory, store, files):
        engine = engine_factory()
        engine.handle(f"@{files / 'labs.txt'}")
        engine.handle("note the CRP please")
        engine.close()

        reopened = store.find(engine.session.id)
        llm = FakeLLM()
        resumed = engine_factory(reopened, llm=llm)
        resumed.handle("remind me of the WBC")
        prompt = llm.prompts[-1][1]
        assert "WBC 15.9" in prompt
        assert "Clinician: note the CRP please" in prompt

    def test_long_history_folds_into_summary(self, engine_factory, monkeypatch):
        import chat.engine as engine_module
        from config import CHAT

        monkeypatch.setattr(engine_module, "CHAT", type(CHAT)(recent_chars=200))
        llm = FakeLLM()
        engine = engine_factory(llm=llm)
        for i in range(5):
            engine.handle(f"question {i} " + "x" * 80)
        meta = engine.session.meta
        assert meta.summary.startswith("Summary:")
        assert meta.summarized_turns > 0
        answers = [p for s, p in llm.prompts if "running memory" not in s]
        assert "## Earlier conversation (summary)\nSummary:" in answers[-1]

    def test_duplicate_upload_is_not_reanalysed(self, engine_factory, files):
        calls, events = [], []
        engine = engine_factory(calls=calls, events=events)
        engine.handle(f"@{files / 'labs.txt'}")
        engine.handle(f"@{files / 'labs.txt'}")
        assert len(calls) == 1
        assert any("already in this session" in e["text"] for e in events)

    def test_everything_emitted_is_logged(self, engine_factory, files):
        events = []
        engine = engine_factory(events=events)
        engine.handle(f"@{files / 'labs.txt'} summarise")
        logged = [json.loads(l)["kind"] for l in engine.session.transcript_path.read_text(encoding="utf-8").splitlines()]
        assert [e["kind"] for e in events] == [k for k in logged if k != "command"]
        terminal = engine.session.terminal_log_path.read_text(encoding="utf-8")
        assert "ASSISTANT" in terminal and "ANALYSIS" in terminal

    @pytest.mark.parametrize("command,expected", [
        ("/help", "Commands"),
        ("/files", "No files"),
        ("/report", "No analysis"),
        ("/history 54M fever 3 days", "Clinical history set"),
        ("/literature on", "enabled"),
        ("/sessions", "patient-001"),
        ("/memory", "Indexed chunks"),
        ("/bogus", "Unknown command"),
    ])
    def test_commands(self, engine_factory, command, expected):
        events = []
        engine = engine_factory(events=events)
        result = engine.handle(command)
        assert result.action == ""
        assert expected in events[-1]["text"]

    @pytest.mark.parametrize("command,action,arg", [
        ("/quit", "quit", ""), ("/new case two", "new", "case two"),
        ("/resume abc", "resume", "abc"), ("/clear", "clear", ""),
    ])
    def test_ui_actions(self, engine_factory, command, action, arg):
        result = engine_factory().handle(command)
        assert (result.action, result.argument) == (action, arg)

    def test_history_is_passed_to_analysis(self, engine_factory, files):
        calls = []
        engine = engine_factory(calls=calls)
        engine.handle("/history 54M, 3 days fever")
        engine.handle(f"@{files / 'labs.txt'}")
        assert calls[0]["history"] == "54M, 3 days fever"


# --------------------------------------------------------------------------
# TUI smoke test (Textual pilot, headless)
# --------------------------------------------------------------------------
def test_tui_renders_help_and_resumes(store):
    from chat.engine import ChatEngine
    from tui.app import MarkdownBubble, MedicalAOSApp, UserBubble

    def factory(session, st, callback):
        return ChatEngine(session, st, on_event=callback, analyzer=fake_analyzer([]), llm=FakeLLM())

    async def scenario():
        app = MedicalAOSApp(name="tui-test", store=store, engine_factory=factory)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press(*"/help")
            await pilot.press("enter")
            for _ in range(50):
                await pilot.pause(0.05)
                if not app._busy:
                    break
            assert any("Commands" in b._text for b in app._base().query(MarkdownBubble))
            await pilot.press(*"hello there", "enter")
            for _ in range(50):
                await pilot.pause(0.05)
                if not app._busy:
                    break
            assert any(b._text == "hello there" for b in app._base().query(UserBubble))
            session_id = app.engine.session.id
        app2 = MedicalAOSApp(resume=session_id, store=store, engine_factory=factory)
        async with app2.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            assert app2.engine.session.id == session_id
            assert any(b._text == "hello there" for b in app2._base().query(UserBubble))

    asyncio.run(scenario())


class TestRetrievalRegressions:
    def test_british_spelling_and_abbreviations_match(self, tmp_path):
        """Live miss: 'haemoglobin' query vs 'Hemoglobin 11.8 g/dL' in the PDF."""
        from chat.memory import Chunk, MemoryIndex

        index = MemoryIndex(tmp_path / "m")
        index.add([
            Chunk(id="cbc", kind="document", source="labs.pdf#page=1", text="Hemoglobin 11.8 g/dL Low"),
            Chunk(id="other", kind="document", source="labs.pdf#page=2", text="Sodium 139 mmol/L"),
        ])
        assert index.search("what was the haemoglobin")[0].id == "cbc"
        assert index.search("Hb?")[0].id == "cbc"
        assert index.search("na level")[0].id == "other"

    def test_small_sessions_put_every_page_in_context(self, engine_factory, files):
        llm = FakeLLM()
        engine = engine_factory(llm=llm)
        engine.handle(f"@{files / 'labs.txt'}")
        engine.handle("zzz unrelated words qqq")  # matches nothing lexically
        assert "CRP 92" in llm.prompts[-1][1]

    def test_past_answers_are_not_presented_as_evidence(self, engine_factory, files):
        llm = FakeLLM()
        engine = engine_factory(llm=llm)
        engine.handle(f"@{files / 'labs.txt'}")
        for i in range(4):
            engine.handle(f"filler question {i}")
        engine.handle("filler question 0 again")
        prompt = llm.prompts[-1][1]
        evidence = prompt.split("## Evidence from this session's files and analysis")[1].split("## Possibly related")[0]
        assert "Clinician asked" not in evidence


def test_tui_session_picker_and_new_session(store):
    from chat.engine import ChatEngine
    from tui.app import MedicalAOSApp, SessionPicker

    def factory(session, st, callback):
        return ChatEngine(session, st, on_event=callback, analyzer=fake_analyzer([]), llm=FakeLLM())

    older = store.create("older case")

    async def scenario():
        app = MedicalAOSApp(name="first", store=store, engine_factory=factory)
        async with app.run_test(size=(120, 40)) as pilot:
            first_id = app.engine.session.id
            await pilot.press("ctrl+o")
            await pilot.pause(0.2)
            assert isinstance(app.screen, SessionPicker)
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert app.engine.session.id == first_id

            await pilot.press(*"/new second case", "enter")
            for _ in range(40):
                await pilot.pause(0.05)
                if not app._busy and app.engine.session.id != first_id:
                    break
            assert app.engine.session.name == "second case"

            await pilot.press(*f"/resume {older.id}", "enter")
            for _ in range(40):
                await pilot.pause(0.05)
                if app.engine.session.id == older.id:
                    break
            assert app.engine.session.id == older.id

    asyncio.run(scenario())
