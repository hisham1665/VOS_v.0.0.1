"""Full-screen chat UI for Medical AOS, built on Textual.

    python main.py --chat                    # new session
    python main.py --chat --name patient-001 # reopen or create patient session
    python main.py --resume                  # pick a saved session
    python main.py --resume a1b2c3           # resume by id (or name)

The app is a thin view over :class:`chat.engine.ChatEngine`: it sends what the
user types to the engine on a worker thread and renders the events the engine
emits. Everything rendered is also in the session's transcript and terminal
log, because the engine writes those itself.
"""

from __future__ import annotations

import os
from pathlib import Path

from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.suggester import Suggester
from textual.widgets import Footer, Input, Markdown, OptionList, Static
from textual.widgets.option_list import Option

from chat.engine import ChatEngine, EngineResult
from chat.session import Session, SessionStore
from core.logging_utils import set_console_logging

LOGO = r"""
 ╔╦╗┌─┐┌┬┐┬┌─┐┌─┐┬    ╔═╗╔═╗╔═╗
 ║║║├┤  │││  ├─┤│    ╠═╣║ ║╚═╗
 ╩ ╩└─┘─┴┘┴└─┘┴ ┴┴─┘  ╩ ╩╚═╝╚═╝
"""

WELCOME = """\
**Attach** scans, PDFs, lab reports, notes — type `@` + a path, or **drag files or folders** into this window.
**Ask** anything about them; follow-up questions remember everything uploaded in this session.

`/help` commands · `/files` uploads · `/report` latest report · `/sessions` · `/resume` · `/new`

> AI-generated draft for clinician review — decision support only, not a diagnosis.
"""

_COMMANDS = [
    "/help", "/attach ", "/analyze", "/history ", "/files", "/report", "/memory", "/differential",
    "/literature on", "/literature off", "/sessions", "/resume ", "/new ", "/rename ",
    "/clear", "/quit",
]

_SEVERITY_CLASS = {
    "critical": "sev-critical", "high": "sev-high", "moderate": "sev-moderate",
    "low": "sev-low", "normal": "sev-normal",
}

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class PromptSuggester(Suggester):
    """Ghost-text completion for slash commands and ``@path`` references."""

    def __init__(self):
        super().__init__(use_cache=False, case_sensitive=True)

    async def get_suggestion(self, value: str) -> str | None:
        if value.startswith("/") and " " not in value.rstrip():
            for command in _COMMANDS:
                if command.startswith(value) and command != value:
                    return command
            return None
        head, _, token = value.rpartition(" ")
        if not token.startswith("@") or token.startswith('@"'):
            return None
        partial = token[1:]
        base = Path(partial).expanduser()
        directory = base if partial.endswith(("/", "\\")) else base.parent
        prefix = "" if partial.endswith(("/", "\\")) else base.name
        try:
            directory = directory if str(directory) not in ("", ".") else Path.cwd()
            names = sorted(p.name + ("/" if p.is_dir() else "") for p in directory.iterdir()
                           if p.name.lower().startswith(prefix.lower()) and not p.name.startswith("."))
        except OSError:
            return None
        if not names:
            return None
        completed = partial[: len(partial) - len(prefix)] + names[0]
        if " " in completed:
            completed = f'"{completed}"'
        return (head + " " if head else "") + "@" + completed


class Message(Static):
    """Base chat bubble."""


class UserBubble(Vertical):
    def __init__(self, text: str, attachments: list[str]):
        super().__init__(classes="bubble user")
        self._text = text
        self._attachments = attachments

    def compose(self) -> ComposeResult:
        yield Static(Text("You", style="bold"), classes="who")
        if self._text:
            yield Static(Text(self._text), classes="body")
        if self._attachments:
            yield Static(Text("  ".join(f"📎 {a}" for a in self._attachments)), classes="chips")


class MarkdownBubble(Vertical):
    def __init__(self, title: str, text: str, classes: str):
        super().__init__(classes=f"bubble {classes}")
        self._title = title
        self._text = text

    def compose(self) -> ComposeResult:
        if self._title:
            yield Static(Text(self._title, style="bold"), classes="who")
        yield Markdown(self._text, classes="body")


class SessionPicker(ModalScreen[str]):
    """Choose a saved session (or start a new one)."""

    BINDINGS = [Binding("escape", "dismiss('')", "Cancel")]

    def __init__(self, store: SessionStore, current: Session | None):
        super().__init__()
        self.store = store
        self.current = current

    def compose(self) -> ComposeResult:
        options = [Option("＋  New session", id="__new__")]
        for s in self.store.list():
            mark = "▶ " if self.current is not None and s.id == self.current.id else "  "
            label = (f"{mark}{s.name}  ·  {s.id}  ·  {len(s.meta.files)} file(s)  ·  "
                     f"{len(s.meta.analyses)} run(s)  ·  {s.meta.updated_at[:16].replace('T', ' ')}")
            options.append(Option(label, id=s.id))
        with Vertical(id="picker"):
            yield Static(Text("Resume a session", style="bold"), id="picker-title")
            yield OptionList(*options, id="picker-list")
            yield Static(Text("↑↓ choose · Enter open · Esc cancel", style="dim"))

    @on(OptionList.OptionSelected)
    def _chosen(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id or "")


class MedicalAOSApp(App):
    TITLE = "Medical AOS"
    CSS_PATH = "app.tcss"
    BINDINGS = [
        Binding("ctrl+b", "toggle_sidebar", "Sidebar"),
        Binding("ctrl+o", "pick_session", "Sessions"),
        Binding("ctrl+n", "new_session", "New"),
        Binding("ctrl+l", "clear_screen", "Clear"),
        Binding("up", "history(-1)", "Prev", show=False),
        Binding("down", "history(1)", "Next", show=False),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, *, resume: str | None = None, name: str | None = None,
                 store: SessionStore | None = None, engine_factory=None):
        super().__init__()
        self.store = store or SessionStore()
        self._resume = resume
        self._name = name
        self._engine_factory = engine_factory or (lambda s, st, cb: ChatEngine(s, st, on_event=cb))
        self.engine: ChatEngine | None = None
        self._history: list[str] = []
        self._history_pos = 0
        self._spin = 0
        self._status = "Ready"
        self._busy = False

    def _base(self):
        """The main chat screen, even while a modal (session picker) is on top."""
        return self.screen_stack[0] if self.screen_stack else self.screen

    def _q(self, selector, expect_type=None):
        base = self._base()
        return base.query_one(selector, expect_type) if expect_type else base.query_one(selector)

    # ------------------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        yield Static(id="topbar")
        with Horizontal(id="main"):
            yield VerticalScroll(id="chat")
            with VerticalScroll(id="sidebar"):
                yield Static(id="side-status")
        with Vertical(id="composer"):
            yield Input(placeholder="Ask a question, or attach with @path / drag files here…   (/help)",
                        id="prompt", suggester=PromptSuggester())
            yield Static(id="statusline")
        yield Footer()

    def on_mount(self) -> None:
        set_console_logging(False)
        self.begin_capture_print(self, stdout=True, stderr=True)
        self.set_interval(0.1, self._tick)
        if self._resume == "__pick__":
            self._open_session(self.store.create(self._name), replay=False)
            self.action_pick_session()
        elif self._resume:
            found = self.store.find(self._resume)
            if found is None:
                self._open_session(self.store.create(self._name), replay=False)
                self._render_event({"kind": "error", "text": f"No session matches `{self._resume}`. Started a new one; `/sessions` lists saved sessions."})
            else:
                self._open_session(found, replay=True)
        else:
            session, resumed = self.store.open_named(self._name)
            self._open_session(session, replay=resumed)
        self._q("#prompt", Input).focus()

    # ------------------------------------------------------------ sessions
    def _open_session(self, session: Session, *, replay: bool) -> None:
        if self.engine is not None:
            self.engine.close()
        self.engine = self._engine_factory(session, self.store, self._on_engine_event)
        chat = self._q("#chat", VerticalScroll)
        chat.remove_children()
        chat.mount(Static(Text(LOGO, style="bold cyan"), classes="logo"))
        chat.mount(MarkdownBubble("", WELCOME, "welcome"))
        self._history = []
        if replay:
            shown = 0
            for event in session.events():
                if event.get("kind") == "user" and event.get("raw"):
                    self._history.append(event["raw"])
                if event.get("kind") in ("user", "assistant", "analysis", "system", "error", "memory"):
                    self._render_event(event, scroll=False)
                    shown += 1
            self._render_event({"kind": "memory", "text": f"Resumed **{session.name}** — {shown} message(s) restored, memory and uploads reloaded."})
        self._history_pos = len(self._history)
        self._refresh_chrome()
        chat.scroll_end(animate=False)

    def action_pick_session(self) -> None:
        if self._busy:
            self.notify("Wait for the current task to finish.", severity="warning")
            return
        self.push_screen(SessionPicker(self.store, self.engine.session if self.engine else None),
                         self._picked)

    def _picked(self, choice: str | None) -> None:
        if not choice:
            return
        if choice == "__new__":
            self.action_new_session()
            return
        found = self.store.find(choice)
        if found is not None:
            self._open_session(found, replay=True)

    def action_new_session(self, name: str | None = None) -> None:
        if self._busy:
            self.notify("Wait for the current task to finish.", severity="warning")
            return
        self._open_session(self.store.create(name), replay=False)

    def action_clear_screen(self) -> None:
        chat = self._q("#chat", VerticalScroll)
        chat.remove_children()

    def action_toggle_sidebar(self) -> None:
        self._q("#sidebar").toggle_class("hidden")

    def action_history(self, step: int) -> None:
        prompt = self._q("#prompt", Input)
        if not prompt.has_focus or not self._history:
            return
        self._history_pos = max(0, min(len(self._history), self._history_pos + step))
        prompt.value = self._history[self._history_pos] if self._history_pos < len(self._history) else ""
        prompt.cursor_position = len(prompt.value)

    # -------------------------------------------------------------- input
    @on(Input.Submitted, "#prompt")
    def _submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        if self._busy:
            self.notify("Still working on the previous message…", severity="warning")
            return
        event.input.value = ""
        self._history.append(text)
        self._history_pos = len(self._history)
        self._set_busy(True, "Working…")
        self.run_worker(lambda: self._process(text), thread=True, exclusive=True, group="engine")

    def _process(self, text: str) -> None:
        try:
            result = self.engine.handle(text)
        except Exception as exc:  # noqa: BLE001 - never kill the UI
            self.engine.emit("error", f"Unexpected error: {type(exc).__name__}: {exc}")
            result = EngineResult()
        self.call_from_thread(self._after, result)

    def _after(self, result: EngineResult) -> None:
        self._set_busy(False, "Ready")
        if result.action == "quit":
            self.exit()
        elif result.action == "clear":
            self.action_clear_screen()
        elif result.action == "new":
            self.action_new_session(result.argument or None)
        elif result.action == "resume":
            if not result.argument:
                self.action_pick_session()
            else:
                found = self.store.find(result.argument)
                if found is None:
                    self._render_event({"kind": "error", "text": f"No unique session matches `{result.argument}`. Try `/sessions`."})
                else:
                    self._open_session(found, replay=True)
        self._refresh_chrome()

    # ------------------------------------------------------------- events
    def _on_engine_event(self, event: dict) -> None:
        """Called from the worker thread for every engine event."""
        self.call_from_thread(self._render_event, event)

    def _render_event(self, event: dict, *, scroll: bool = True) -> None:
        kind = event.get("kind")
        text = event.get("text", "")
        chat = self._q("#chat", VerticalScroll)
        widget = None
        if kind == "user":
            widget = UserBubble(text if text != "(attached files)" else "", event.get("attachments") or [])
        elif kind == "assistant":
            model = event.get("model") or ""
            widget = MarkdownBubble(f"Medical AOS{'  ·  ' + model if model else ''}", text, "assistant")
        elif kind == "analysis":
            severity = _SEVERITY_CLASS.get(event.get("status", ""), "")
            widget = MarkdownBubble("Analysis", text, f"analysis {severity}")
        elif kind == "system":
            widget = MarkdownBubble("", text, "system")
        elif kind == "memory":
            widget = MarkdownBubble("", f"🧠 {text}", "system")
        elif kind == "error":
            widget = MarkdownBubble("", f"**Error:** {text}", "error")
        elif kind == "progress":
            self._status = text
            return
        if widget is not None:
            chat.mount(widget)
            if scroll:
                chat.scroll_end(animate=False)
        self._refresh_chrome()

    def on_print(self, event: events.Print) -> None:
        """stdout/stderr from libraries: keep it off-screen, but logged."""
        if self.engine is None or not event.text.strip():
            return
        try:
            with open(self.engine.session.logs_dir / "console.log", "a", encoding="utf-8") as handle:
                handle.write(event.text)
        except OSError:
            pass

    # -------------------------------------------------------------- chrome
    def _set_busy(self, busy: bool, status: str) -> None:
        self._busy = busy
        self._status = status
        self._q("#prompt", Input).disabled = busy
        if not busy:
            self._q("#prompt", Input).focus()

    def _tick(self) -> None:
        try:
            line = self._q("#statusline", Static)
        except NoMatches:  # screen being torn down or a modal on top
            return
        if self._busy:
            self._spin = (self._spin + 1) % len(_SPINNER)
            line.update(Text.assemble((f" {_SPINNER[self._spin]} ", "bold cyan"), (self._status[:160], "italic")))
        else:
            line.update(Text(" ● Ready", style="green"))

    def _refresh_chrome(self) -> None:
        if self.engine is None:
            return
        session = self.engine.session
        meta = session.meta
        last = meta.analyses[-1] if meta.analyses else None
        top = Text.assemble(
            (" ✚ Medical AOS ", "bold black on cyan"), "  ",
            (session.name, "bold"), ("  ·  ", "dim"), (f"id {session.id}", "cyan"),
            ("  ·  ", "dim"), f"{len(meta.files)} file(s)", ("  ·  ", "dim"),
            f"{len(meta.analyses)} run(s)",
        )
        if last:
            top.append("  ·  ", style="dim")
            top.append(f"{last.status.upper()} / {last.triage}", style=_status_style(last.status))
        self._q("#topbar", Static).update(top)

        side = Text()
        side.append("Session\n", style="bold underline")
        side.append(f"{session.name}\n", style="bold")
        side.append(f"id {session.id}\n", style="cyan")
        side.append(f"{session.folder}\n\n", style="dim")
        side.append("History\n", style="bold underline")
        side.append((meta.clinical_history or "not set — /history <text>") + "\n\n")
        if last:
            side.append("Latest analysis\n", style="bold underline")
            side.append(f"run {last.run}: ", style="dim")
            side.append(f"{last.status.upper()}", style=_status_style(last.status))
            side.append(f" · triage {last.triage}\n\n")
        side.append("Memory\n", style="bold underline")
        side.append(f"{len(self.engine.memory.chunks)} indexed chunk(s)\n")
        side.append(f"{meta.turns} Q&A turn(s), {meta.summarized_turns} folded\n")
        hypotheses = meta.clinical_assessment.get("differential", [])
        if hypotheses:
            excluded = sum(h.get("status") == "ruled_out" for h in hypotheses)
            side.append(f"{len(hypotheses) - excluded} active, {excluded} ruled out — /differential\n")
        side.append("\nFiles\n", style="bold underline")
        if not meta.files:
            side.append("none yet — @path to attach\n", style="dim")
        for i, f in enumerate(meta.files, 1):
            side.append(f"{i:>2}. ", style="dim")
            side.append(f"{f.name}\n")
            side.append(f"    {f.kind} · {f.size / 1024:.0f} KB\n", style="dim")
        self._q("#side-status", Static).update(side)

    def on_unmount(self) -> None:
        if self.engine is not None:
            self.engine.close()


def _status_style(status: str) -> str:
    return {
        "critical": "bold white on red", "high": "bold black on dark_orange",
        "moderate": "bold black on yellow", "low": "bold white on blue",
        "normal": "bold black on green",
    }.get(status, "bold")


def run(resume: str | None = None, name: str | None = None) -> int:
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    MedicalAOSApp(resume=resume, name=name).run()
    return 0
