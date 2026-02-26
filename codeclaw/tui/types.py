"""Shared types for the CodeClaw TUI subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass
class CommandResult:
    """Standard command execution result."""

    ok: bool
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


CommandHandler = Callable[[Any, list[str]], CommandResult]


@dataclass
class SlashCommand:
    """Slash command definition."""

    name: str
    handler: CommandHandler
    help_text: str
    usage: str | None = None
    aliases: tuple[str, ...] = ()
    source: str = "builtin"


@dataclass
class JobEvent:
    """One event emitted by the background job manager."""

    job_id: str
    kind: str
    message: str = ""
    progress: float | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)


@dataclass
class JobInfo:
    """Current snapshot of a background job."""

    id: str
    name: str
    status: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    cancel_requested: bool = False

