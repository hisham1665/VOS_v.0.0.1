"""Command registry shared by the Textual UI and testable without Textual."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from aos_v0.core.artifacts import ArtifactError


@dataclass(frozen=True)
class Command:
    name: str
    help: str
    handler: Callable[[list[str]], str]


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, name: str, help_text: str, handler: Callable[[list[str]], str]) -> None:
        self._commands[name] = Command(name, help_text, handler)

    def execute(self, line: str) -> str | None:
        # Do not use shlex here. Its POSIX lexer consumes Windows backslashes;
        # its non-POSIX mode has surprising quote retention. This deliberately
        # small parser supports the CLI contract: whitespace-delimited values
        # and double-quoted values containing spaces, without altering paths.
        if line.count('"') % 2:
            return "✗ Invalid command: unmatched quote"
        parts = [quoted or bare for quoted, bare in re.findall(r'"([^"]*)"|(\S+)', line)]
        if not parts or not parts[0].startswith("/"):
            return None
        command = self._commands.get(parts[0][1:])
        return command.handler(parts[1:]) if command else f"✗ Unknown command: {parts[0]}"

    def help_text(self) -> str:
        return "\n".join(f"/{item.name:<13} {item.help}" for item in self._commands.values())


def build_registry(service, clear: Callable[[], None], quit_app: Callable[[], None]) -> CommandRegistry:
    registry = CommandRegistry()
    registry.register("help", "Show available commands", lambda _: registry.help_text())
    registry.register("clear", "Clear the conversation", lambda _: (clear(), "Conversation cleared")[1])
    registry.register("history", "Show submitted requests", lambda _: "\n".join(r.user_input for r in service.session.requests) or "No requests yet")
    registry.register("session", "Show session metrics", lambda _: "\n".join(f"{key}: {value}" for key, value in service.session.summary().items()))
    registry.register("status", "Show system status", lambda _: "AOS kernel ready")
    registry.register("agents", "Show registered executable resources", lambda _: "Resource inventory is available during a request")
    registry.register("capabilities", "Show capability DNA vocabulary", lambda _: "Capability DNA is generated per task during execution")
    registry.register("files", "Show session artifacts", lambda _: "\n".join(f"{a.id}  {a.name}  {a.artifact_type}" for a in service.session.artifacts) or "No uploaded artifacts")
    def upload(args: list[str]) -> str:
        if not args:
            return "Usage: /upload <path> [path ...]"
        try:
            artifacts = service.upload(args)
        except ArtifactError as exc:
            return f"✗ {exc}"
        return "\n".join(f"✓ {a.name}\n  type: {a.artifact_type}\n  size: {a.size:,} bytes\n  artifact: {a.id}" for a in artifacts)
    registry.register("upload", "Upload one or more files", upload)
    registry.register("config", "Show runtime configuration", lambda _: "Budget is configurable through the standard CLI --budget flag")
    registry.register("quit", "Exit AOS", lambda _: (quit_app(), "Goodbye")[1])
    return registry
