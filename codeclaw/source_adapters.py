"""Source adapter registry and diagnostics for multi-IDE ingestion."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPERIMENTAL = "experimental"
STABLE = "stable"


@dataclass(frozen=True)
class SourceAdapter:
    name: str
    tier: str
    roots: tuple[Path, ...]
    file_globs: tuple[str, ...] = ("*.jsonl", "*.json")


def _home() -> Path:
    return Path.home()


def _adapter_roots() -> dict[str, SourceAdapter]:
    home = _home()
    return {
        "cursor": SourceAdapter(
            name="cursor",
            tier=EXPERIMENTAL,
            roots=(home / ".cursor" / "sessions",),
        ),
        "windsurf": SourceAdapter(
            name="windsurf",
            tier=EXPERIMENTAL,
            roots=(home / ".windsurf" / "sessions",),
        ),
        "aider": SourceAdapter(
            name="aider",
            tier=EXPERIMENTAL,
            roots=(home / ".aider" / "sessions",),
        ),
        "continue": SourceAdapter(
            name="continue",
            tier=EXPERIMENTAL,
            roots=(home / ".continue" / "sessions",),
        ),
        "antigravity": SourceAdapter(
            name="antigravity",
            tier=EXPERIMENTAL,
            roots=(home / ".antigravity" / "sessions",),
        ),
        "vscode": SourceAdapter(
            name="vscode",
            tier=EXPERIMENTAL,
            roots=(
                home / "AppData" / "Roaming" / "Code" / "User" / "globalStorage" / "codeclaw" / "sessions",
                home / ".config" / "Code" / "User" / "globalStorage" / "codeclaw" / "sessions",
            ),
        ),
        "zed": SourceAdapter(
            name="zed",
            tier=EXPERIMENTAL,
            roots=(home / ".zed" / "sessions",),
        ),
        "xcode-beta": SourceAdapter(
            name="xcode-beta",
            tier=EXPERIMENTAL,
            roots=(home / "Library" / "Developer" / "XcodeBeta" / "UserData" / "AgentSessions",),
        ),
    }


def iter_external_adapters() -> list[SourceAdapter]:
    return list(_adapter_roots().values())


def _iter_adapter_files(adapter: SourceAdapter) -> list[Path]:
    files: list[Path] = []
    for root in adapter.roots:
        if not root.exists():
            continue
        for pattern in adapter.file_globs:
            files.extend(sorted(root.rglob(pattern)))
    return files


def discover_external_projects() -> list[dict[str, Any]]:
    projects: list[dict[str, Any]] = []
    for adapter in iter_external_adapters():
        by_project: dict[str, list[Path]] = {}
        for file in _iter_adapter_files(adapter):
            parent = file.parent
            key = str(parent)
            by_project.setdefault(key, []).append(file)

        for key, files in by_project.items():
            project_name = Path(key).name or "unknown"
            projects.append(
                {
                    "dir_name": key,
                    "display_name": f"{adapter.name}:{project_name}",
                    "session_count": len(files),
                    "total_size_bytes": sum(f.stat().st_size for f in files if f.exists()),
                    "source": adapter.name,
                    "adapter_tier": adapter.tier,
                }
            )
    return sorted(projects, key=lambda p: (p["display_name"], p["source"]))


def _normalize_timestamp(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    return datetime.now(tz=timezone.utc).isoformat()


def parse_external_project_sessions(
    source: str,
    project_dir_name: str,
    anonymizer: Any,
    include_thinking: bool = True,
) -> list[dict[str, Any]]:
    """Best-effort parser for non-Claude/Codex IDE adapters."""
    project_path = Path(project_dir_name)
    if not project_path.exists():
        return []

    sessions: list[dict[str, Any]] = []
    for file in sorted([*project_path.glob("*.jsonl"), *project_path.glob("*.json")]):
        try:
            raw = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = raw.splitlines() if file.suffix.lower() == ".jsonl" else [raw]
        messages: list[dict[str, Any]] = []
        start_time = None
        end_time = None
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue

            if isinstance(item, dict) and isinstance(item.get("messages"), list):
                for msg in item["messages"]:
                    if not isinstance(msg, dict):
                        continue
                    role = str(msg.get("role", "user"))
                    content = anonymizer.text(str(msg.get("content", "") or ""))
                    entry = {
                        "role": role,
                        "content": content,
                        "timestamp": _normalize_timestamp(msg.get("timestamp")),
                    }
                    if include_thinking and "thinking" in msg:
                        entry["thinking"] = anonymizer.text(str(msg.get("thinking", "")))
                    if msg.get("tool_uses"):
                        entry["tool_uses"] = msg.get("tool_uses", [])
                    messages.append(entry)
                continue

            role = str(item.get("role") or item.get("type") or "user")
            content = str(item.get("content") or item.get("text") or item.get("message") or "")
            if not content.strip():
                continue
            timestamp = _normalize_timestamp(item.get("timestamp"))
            if start_time is None:
                start_time = timestamp
            end_time = timestamp
            messages.append(
                {
                    "role": role,
                    "content": anonymizer.text(content.strip()),
                    "timestamp": timestamp,
                }
            )

        if not messages:
            continue
        input_tokens = sum(max(1, len(m.get("content", "").split())) for m in messages if m.get("role") == "user")
        output_tokens = sum(max(1, len(m.get("content", "").split())) for m in messages if m.get("role") != "user")
        sessions.append(
            {
                "session_id": file.stem,
                "project": f"{source}:{project_path.name or 'unknown'}",
                "model": f"{source}-unknown",
                "git_branch": None,
                "start_time": start_time,
                "end_time": end_time or start_time,
                "messages": messages,
                "stats": {
                    "user_messages": sum(1 for m in messages if m.get("role") == "user"),
                    "assistant_messages": sum(1 for m in messages if m.get("role") != "user"),
                    "tool_uses": sum(len(m.get("tool_uses", [])) for m in messages),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
                "source": source,
                "adapter_tier": _adapter_roots().get(source, SourceAdapter(source, EXPERIMENTAL, tuple())).tier,
            }
        )
    return sessions


def adapter_diagnostics() -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for adapter in iter_external_adapters():
        files = _iter_adapter_files(adapter)
        found_roots = [str(root) for root in adapter.roots if root.exists()]
        diagnostics.append(
            {
                "adapter": adapter.name,
                "tier": adapter.tier,
                "platform": os.name,
                "roots": [str(root) for root in adapter.roots],
                "roots_found": found_roots,
                "detected_files": len(files),
                "parse_success_rate_hint": (
                    "high" if adapter.tier == STABLE else "experimental"
                ),
                "ready": len(files) > 0,
            }
        )
    return diagnostics
