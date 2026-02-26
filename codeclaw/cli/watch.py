"""Watch daemon subcommand handlers."""

from __future__ import annotations

import json
import time

from ..config import load_config, save_config
from ..parser import discover_projects
from ._helpers import (
    _filter_projects_by_source,
    _normalize_source_filter,
    _parse_csv_arg,
    _resolve_source_choice,
)


def _set_connected_projects(selection: str, source_filter: str) -> dict:
    config = load_config()
    source_choice, source_explicit = _resolve_source_choice(source_filter, config)
    projects = _filter_projects_by_source(
        discover_projects(),
        _normalize_source_filter(source_choice),
    )
    available = sorted(str(project.get("display_name", "")) for project in projects)

    normalized = selection.strip()
    mode = "connected_only"
    invalid_requested: list[str] = []
    if normalized.lower() in {"all", "clear", "*"}:
        connected: list[str] = []
        mode = "all_projects"
    else:
        requested = _parse_csv_arg(selection) or []
        invalid_requested = [name for name in requested if name not in available]
        connected = sorted(name for name in requested if name in available)

    config["connected_projects"] = connected
    config["projects_confirmed"] = True
    if source_explicit:
        config["source"] = source_choice
    save_config(config)
    return {
        "ok": True,
        "action": "set_projects",
        "source": source_choice,
        "source_selection_confirmed": source_explicit,
        "scope_mode": mode,
        "connected_projects": connected,
        "available_projects": available,
        "invalid_requested": invalid_requested,
    }


def _print_logs(lines: int, follow: bool, interval: float) -> None:
    from ..daemon import LOG_FILE, read_recent_logs

    recent = read_recent_logs(lines=max(1, lines))
    if recent:
        print("\n".join(recent))
    else:
        print("(no daemon logs yet)")

    if not follow:
        return

    if not LOG_FILE.exists():
        print("Waiting for log file to appear...")
    last_size = LOG_FILE.stat().st_size if LOG_FILE.exists() else 0
    try:
        while True:
            if not LOG_FILE.exists():
                time.sleep(max(0.25, interval))
                continue
            size = LOG_FILE.stat().st_size
            if size < last_size:
                last_size = 0
            if size == last_size:
                time.sleep(max(0.25, interval))
                continue
            with open(LOG_FILE, encoding="utf-8", errors="replace") as fh:
                fh.seek(last_size)
                chunk = fh.read()
            if chunk:
                print(chunk, end="" if chunk.endswith("\n") else "\n")
            last_size = size
            time.sleep(max(0.25, interval))
    except KeyboardInterrupt:
        print("\nStopped log follow.")


def _render_monitor(lines: int) -> str:
    from ..daemon import daemon_status, read_recent_logs

    status = daemon_status()
    state = status.get("state", {}) if isinstance(status.get("state"), dict) else {}
    recent = read_recent_logs(lines=max(1, lines))
    header = [
        "CodeClaw Watch Monitor",
        f"running={status.get('running')} pid={status.get('pid')} paused={status.get('paused')}",
        f"source={status.get('source')} pending_sessions={status.get('pending_sessions')}",
        f"connected_projects={len(status.get('connected_projects', []))}",
        f"last_result={state.get('last_result')} last_poll_at={state.get('last_poll_at')}",
    ]
    if status.get("connected_projects"):
        header.append("scope=" + ", ".join(status.get("connected_projects", [])))
    else:
        header.append("scope=all discovered projects")
    if state.get("last_error"):
        header.append(f"last_error={state.get('last_error')}")
    header.append("-" * 72)
    header.append("Recent daemon logs:")
    if recent:
        header.extend(recent)
    else:
        header.append("(no daemon logs yet)")
    return "\n".join(header)


def _run_monitor(lines: int, follow: bool, interval: float) -> None:
    print(_render_monitor(lines))
    if not follow:
        return
    try:
        while True:
            time.sleep(max(0.5, interval))
            print()
            print(_render_monitor(lines))
    except KeyboardInterrupt:
        print("\nStopped monitor.")


def _handle_watch(args) -> None:
    from ..daemon import (
        daemon_status,
        set_watch_paused,
        start_daemon,
        stop_daemon,
        trigger_sync_now,
    )

    if args.start:
        print(json.dumps(start_daemon(), indent=2))
        return
    if args.stop:
        print(json.dumps(stop_daemon(), indent=2))
        return
    if args.status:
        print(json.dumps(daemon_status(), indent=2))
        return
    if args.now:
        print(json.dumps(trigger_sync_now(), indent=2))
        return
    if args.pause:
        print(json.dumps(set_watch_paused(True), indent=2))
        return
    if args.resume:
        print(json.dumps(set_watch_paused(False), indent=2))
        return
    if args.logs:
        _print_logs(lines=args.lines, follow=args.follow, interval=args.interval)
        return
    if args.monitor:
        _run_monitor(lines=args.lines, follow=args.follow, interval=args.interval)
        return
    if args.switch_project:
        print(json.dumps(_set_connected_projects(args.switch_project, args.source), indent=2))
        return
    if args.set_projects:
        print(json.dumps(_set_connected_projects(args.set_projects, args.source), indent=2))
        return


def _run_setup_wizard(args) -> None:
    """Backward-compatible wrapper to the guided setup handler."""
    from .setup import handle_setup

    handle_setup(args)
