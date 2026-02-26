"""Slash command registry and parsing helpers for the TUI."""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from .types import SlashCommand


@dataclass
class ParsedCommand:
    """Parsed slash command invocation."""

    raw: str
    command_name: str
    args: list[str]


class CommandRegistry:
    """Stores slash commands and handles parsing/lookup."""

    def __init__(self) -> None:
        self._commands: dict[str, SlashCommand] = {}
        self._aliases: dict[str, str] = {}

    def register(self, command: SlashCommand) -> None:
        name = command.name.strip().lower()
        if not name:
            raise ValueError("Command name cannot be blank.")
        if name in self._commands:
            raise ValueError(f"Command already registered: {name}")
        self._commands[name] = command
        for alias in command.aliases:
            normalized = alias.strip().lower()
            if not normalized:
                continue
            if normalized in self._commands or normalized in self._aliases:
                raise ValueError(f"Command alias already registered: {normalized}")
            self._aliases[normalized] = name

    def unregister(self, name: str) -> bool:
        normalized = name.strip().lower()
        resolved = self.resolve_name(normalized)
        if resolved is None:
            return False
        del self._commands[resolved]
        alias_keys = [key for key, value in self._aliases.items() if value == resolved]
        for key in alias_keys:
            del self._aliases[key]
        return True

    def unregister_by_source(self, source: str) -> int:
        source_tag = source.strip().lower()
        names = [
            name
            for name, command in self._commands.items()
            if command.source.strip().lower() == source_tag
        ]
        removed = 0
        for name in names:
            if self.unregister(name):
                removed += 1
        return removed

    def resolve_name(self, name: str) -> str | None:
        normalized = name.strip().lower()
        if normalized in self._commands:
            return normalized
        return self._aliases.get(normalized)

    def get(self, name: str) -> SlashCommand | None:
        resolved = self.resolve_name(name)
        if resolved is None:
            return None
        return self._commands[resolved]

    def command_names(self) -> list[str]:
        return sorted(self._commands.keys())

    def help_rows(self) -> list[tuple[str, str, str | None]]:
        rows = []
        for name in self.command_names():
            command = self._commands[name]
            alias_suffix = ""
            if command.aliases:
                alias_suffix = f" (aliases: {', '.join(command.aliases)})"
            rows.append((f"/{name}{alias_suffix}", command.help_text, command.usage))
        return rows

    def parse(self, text: str) -> ParsedCommand | None:
        raw = text.strip()
        if not raw.startswith("/"):
            return None
        try:
            parts = shlex.split(raw)
        except ValueError as exc:
            raise ValueError(f"Command parse error: {exc}") from exc
        if not parts:
            return None
        head = parts[0].strip()
        if head == "/":
            raise ValueError("Command name is required after '/'.")
        command_name = head[1:].strip().lower()
        if not command_name:
            raise ValueError("Command name is required after '/'.")
        return ParsedCommand(raw=raw, command_name=command_name, args=parts[1:])

    def completions(self, text_before_cursor: str) -> list[str]:
        text = text_before_cursor.strip()
        if not text.startswith("/"):
            return []
        if " " in text:
            return []
        prefix = text[1:].lower()
        return [f"/{name}" for name in self.command_names() if name.startswith(prefix)]

