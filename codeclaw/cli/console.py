"""Interactive terminal console for managing CodeClaw in one session."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from types import SimpleNamespace

from ..config import load_config, save_config
from ..parser import discover_projects
from ._helpers import _filter_projects_by_source, _normalize_source_filter, _resolve_source_choice, SOURCE_CHOICES
from .growth import handle_doctor, handle_stats


@dataclass
class _ConsoleState:
    source: str


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, indent=2))


def _print_banner(state: _ConsoleState) -> None:
    from ..daemon import daemon_status

    status = daemon_status()
    print("=" * 72)
    print("CodeClaw Console")
    print("Slash commands: /help  /status  /start  /stop  /pause  /resume")
    print("                /sync  /logs  /projects  /scope  /source  /doctor")
    print("                /stats  /run  /clear  /exit")
    print("-" * 72)
    print(
        f"source={state.source} running={status.get('running')} "
        f"paused={status.get('paused')} pending={status.get('pending_sessions')}"
    )
    connected = status.get("connected_projects", [])
    print(
        "connected_scope="
        + (", ".join(connected) if connected else "all discovered projects")
    )
    print("=" * 72)


def _discover_source_projects(source_choice: str) -> list[dict]:
    projects = discover_projects()
    return _filter_projects_by_source(projects, _normalize_source_filter(source_choice))


def _print_projects(source_choice: str) -> list[str]:
    projects = _discover_source_projects(source_choice)
    names = sorted(str(project.get("display_name", "")) for project in projects)
    if not names:
        print(f"No projects found for source '{source_choice}'.")
        return []
    print("Available projects:")
    for idx, name in enumerate(names, start=1):
        print(f"  {idx}. {name}")
    return names


def _set_scope(selection: str, source_choice: str) -> dict[str, object]:
    names = _print_projects(source_choice)
    if not names:
        return {
            "ok": False,
            "error": "No projects are available for the current source scope.",
            "source": source_choice,
        }

    token = selection.strip()
    if not token:
        return {
            "ok": False,
            "error": "Missing project name or index for /scope.",
            "hint": "Use /projects first, then /scope <index|name|all>.",
        }

    connected: list[str]
    if token.lower() in {"all", "clear", "*"}:
        connected = []
        scope_mode = "all_projects"
    elif token.isdigit() and 1 <= int(token) <= len(names):
        connected = [names[int(token) - 1]]
        scope_mode = "connected_only"
    elif token in names:
        connected = [token]
        scope_mode = "connected_only"
    else:
        return {
            "ok": False,
            "error": f"Project not found: {token}",
            "hint": "Use /projects and choose a valid index or exact name.",
        }

    config = load_config()
    config["connected_projects"] = connected
    config["projects_confirmed"] = True
    if source_choice in {"claude", "codex", "both"}:
        config["source"] = source_choice
    save_config(config)
    return {
        "ok": True,
        "source": source_choice,
        "scope_mode": scope_mode,
        "connected_projects": connected,
    }


def _print_help() -> None:
    print(
        "\n".join(
            [
                "Console commands:",
                "  /help                      Show this help",
                "  /status                    Show watch status JSON",
                "  /start | /stop             Start or stop watcher daemon",
                "  /pause | /resume           Pause or resume daemon polling",
                "  /sync                      Trigger immediate sync cycle",
                "  /logs [N]                  Show last N daemon log lines (default 40)",
                "  /projects                  List discovered projects for current source",
                "  /scope <index|name|all>    Set connected project scope quickly",
                "  /source <auto|claude|codex|both>  Change active source filter",
                "  /doctor                    Run setup diagnostics",
                "  /stats [--skill]           Show project/session stats",
                "  /run <codeclaw args...>    Run any standard command, e.g. /run export --no-push",
                "  /clear                     Clear terminal",
                "  /exit                      Exit console",
            ]
        )
    )


def _run_cli_passthrough(rest: list[str]) -> int:
    cmd = [sys.executable, "-m", "codeclaw", *rest]
    proc = subprocess.run(cmd, check=False)  # noqa: S603
    return int(proc.returncode)


def _invoke_growth_handler(handler, namespace: argparse.Namespace) -> None:
    try:
        handler(namespace)
    except SystemExit:
        return


def handle_console(args) -> None:
    config = load_config()
    source_choice, source_explicit = _resolve_source_choice(getattr(args, "source", "auto"), config)
    if not source_explicit and source_choice not in SOURCE_CHOICES:
        source_choice = "both"
    state = _ConsoleState(source=source_choice)
    _print_banner(state)

    while True:
        try:
            raw = input("codeclaw> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting console.")
            return
        if not raw:
            continue
        if not raw.startswith("/"):
            print("Use slash commands. Run /help.")
            continue

        try:
            parts = shlex.split(raw)
        except ValueError as exc:
            print(f"Parse error: {exc}")
            continue
        if not parts:
            continue
        cmd = parts[0].lower()
        tail = parts[1:]

        if cmd in {"/exit", "/quit"}:
            print("Bye.")
            return
        if cmd in {"/help", "/?"}:
            _print_help()
            continue
        if cmd in {"/clear", "/cls"}:
            os.system("cls" if os.name == "nt" else "clear")
            _print_banner(state)
            continue
        if cmd == "/status":
            from ..daemon import daemon_status
            _print_json(daemon_status())
            continue
        if cmd == "/start":
            from ..daemon import start_daemon
            _print_json(start_daemon())
            continue
        if cmd == "/stop":
            from ..daemon import stop_daemon
            _print_json(stop_daemon())
            continue
        if cmd == "/pause":
            from ..daemon import set_watch_paused
            _print_json(set_watch_paused(True))
            continue
        if cmd == "/resume":
            from ..daemon import set_watch_paused
            _print_json(set_watch_paused(False))
            continue
        if cmd in {"/sync", "/now"}:
            from ..daemon import trigger_sync_now
            _print_json(trigger_sync_now())
            continue
        if cmd == "/logs":
            from ..daemon import read_recent_logs
            count = 40
            if tail and tail[0].isdigit():
                count = max(1, int(tail[0]))
            lines = read_recent_logs(lines=count)
            if not lines:
                print("(no daemon logs yet)")
            else:
                print("\n".join(lines))
            continue
        if cmd == "/projects":
            _print_projects(state.source)
            continue
        if cmd == "/scope":
            if not tail:
                print("Usage: /scope <index|name|all>")
                continue
            _print_json(_set_scope(" ".join(tail), state.source))
            continue
        if cmd == "/source":
            if not tail:
                print(f"Current source: {state.source}")
                continue
            candidate = tail[0].lower()
            if candidate not in SOURCE_CHOICES:
                print(f"Invalid source '{candidate}'. Allowed: {', '.join(SOURCE_CHOICES)}")
                continue
            state.source = candidate
            config = load_config()
            if candidate in {"claude", "codex", "both"}:
                config["source"] = candidate
                save_config(config)
            print(f"Source set to: {candidate}")
            continue
        if cmd == "/doctor":
            _invoke_growth_handler(handle_doctor, SimpleNamespace(source=state.source))
            continue
        if cmd == "/stats":
            skill = "--skill" in tail
            _invoke_growth_handler(handle_stats, SimpleNamespace(source=state.source, skill=skill))
            continue
        if cmd == "/run":
            if not tail:
                print("Usage: /run <codeclaw-args...>")
                continue
            rc = _run_cli_passthrough(tail)
            print(f"(exit_code={rc})")
            continue
        if cmd in {"/home", "/dashboard"}:
            _print_banner(state)
            continue

        print(f"Unknown command: {cmd}. Run /help.")
