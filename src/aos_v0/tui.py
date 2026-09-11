"""Textual presentation layer for AOS events (optional dependency)."""

from __future__ import annotations

import threading

from aos_v0.interactive import InteractiveService
from aos_v0.tui_commands import build_registry


def format_event(event) -> str:
    """Pure event renderer, testable without Textual installed."""
    data = event.payload
    if event.type.value == "agent_selected":
        candidates = "\n".join(
            f"  {item['resource']}  score: {item['score']:.3f}"
            for item in data["candidates"]
        )
        return f"Routing\n{data['resource']}\n{candidates}"
    if event.type.value == "agent_completed":
        return f"{data['agent']} ({data['node_id']}) {data['elapsed_ms']}ms"
    if event.type.value == "result_ready":
        return f"Result\n{data['result']}"
    return event.type.value


def main() -> None:
    try:
        from textual.app import App, ComposeResult
        from textual.containers import VerticalScroll
        from textual.widgets import Footer, Header, Input, Static
    except ImportError as exc:
        raise SystemExit("The interactive TUI requires Textual. Install with: pip install -e .[tui]") from exc

    class AOSTui(App):
        CSS = """
        #stream { height: 1fr; padding: 1 2; } Input { dock: bottom; } .event { margin-bottom: 1; }
        """
        TITLE = "AOS — Adaptive Multi-AI Agent Orchestration"

        def __init__(self) -> None:
            super().__init__()
            self.service = InteractiveService()
            self.registry = build_registry(self.service, self._clear_stream, self.exit)
            self.service.subscribe(self._on_event)
            self._awaiting_upload = False

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            yield VerticalScroll(Static(f"AOS  •  Session: {self.service.session.session_id}  •  ready", classes="event"), id="stream")
            yield Input(placeholder="AOS › Ask naturally, or type /help", id="prompt")
            yield Footer()

        def _stream(self): return self.query_one("#stream", VerticalScroll)
        def _write(self, text: str) -> None: self._stream().mount(Static(text, classes="event"))
        def _clear_stream(self) -> None: self._stream().remove_children()

        def _on_event(self, event) -> None:
            # executor events arrive from worker threads; Textual owns rendering.
            self.call_from_thread(self._write, self._format_event(event))

        @staticmethod
        def _format_event(event) -> str:
            data = event.payload
            if event.type.value == "capability_detected":
                return "── Capability DNA ──\n" + "\n".join("✓ " + flag for node in data["nodes"] for flag in node["flags"])
            if event.type.value == "agent_selected":
                candidates = "\n".join(f"  {item['resource']}  score: {item['score']:.3f}" for item in data["candidates"])
                return f"── Routing ──\n→ {data['resource']}\n{candidates}"
            if event.type.value == "agent_started": return f"⟳ {data['agent']} ({data['node_id']}) running"
            if event.type.value == "agent_completed": return f"✓ {data['agent']} ({data['node_id']}) {data['elapsed_ms']}ms"
            if event.type.value == "agent_failed": return f"✗ {data['agent']} ({data['node_id']}) {data['status']}"
            if event.type.value == "recovery_started": return f"Recovery initiated: {data['failure_class']}"
            if event.type.value == "recovery_completed": return "✓ Recovery successful" if data["recovered"] else "✗ Recovery exhausted"
            if event.type.value == "result_ready": return f"── Result ──\n{data['result']}"
            return ""

        def on_input_submitted(self, message: Input.Submitted) -> None:
            line = message.value.strip(); message.input.value = ""
            if not line: return
            if self._awaiting_upload:
                self._awaiting_upload = False
                # The follow-up accepts either a bare path or a quoted path.
                # Normalize the latter before wrapping it for command parsing.
                result = self.registry.execute(f'/upload "{line.strip(chr(34))}"')
                self._write(result or "")
                return
            if line == "/upload":
                self._awaiting_upload = True
                self._write("Enter a file path to upload:")
                return
            command_result = self.registry.execute(line)
            if command_result is not None:
                self._write(command_result); return
            self._write(f"AOS › {line}")
            threading.Thread(target=self._submit, args=(line,), daemon=True).start()

        def _submit(self, prompt: str) -> None:
            try: self.service.submit(prompt)
            except Exception as exc: self.call_from_thread(self._write, f"✗ Request failed: {exc}")

    AOSTui().run()


if __name__ == "__main__":
    main()
