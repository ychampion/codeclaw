"""Connected project scope management for CodeClaw."""

from __future__ import annotations

import json
import sys

from ..config import load_config, save_config
from ..parser import detect_current_project, discover_projects
from ._helpers import (
    _filter_projects_by_source,
    _normalize_source_filter,
    _parse_csv_arg,
    _resolve_source_choice,
)


def _discover_projects_for_scope(source_choice: str) -> tuple[list[dict], str | None]:
    source_filter = _normalize_source_filter(source_choice)
    try:
        projects = _filter_projects_by_source(discover_projects(), source_filter)
    except Exception as exc:  # pragma: no cover - defensive path
        return [], f"{type(exc).__name__}: {exc}"
    return projects, None


def handle_projects(args) -> None:
    """Show and manage the connected project list."""
    config = load_config()
    source_choice, source_explicit = _resolve_source_choice(args.source, config)
    projects, discovery_error = _discover_projects_for_scope(source_choice)
    if discovery_error is not None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "Project discovery failed.",
                    "detail": discovery_error,
                    "source": source_choice,
                },
                indent=2,
            )
        )
        sys.exit(1)

    available_names = sorted(str(project.get("display_name", "")) for project in projects)
    connected = {
        str(name).strip()
        for name in config.get("connected_projects", [])
        if str(name).strip()
    }

    updated = False
    warnings: list[str] = []

    if args.clear:
        connected.clear()
        updated = True

    if args.all:
        connected = set(available_names)
        updated = True

    if args.use_current:
        current = detect_current_project()
        if current and current.get("display_name"):
            connected = {str(current["display_name"])}
            updated = True
        else:
            warnings.append("Current project could not be detected from the working directory.")

    for name in _parse_csv_arg(args.connect) or []:
        if name in available_names:
            connected.add(name)
            updated = True
        else:
            warnings.append(f"Project not found in selected source scope: {name}")

    for name in _parse_csv_arg(args.disconnect) or []:
        if name in connected:
            connected.remove(name)
            updated = True
        else:
            warnings.append(f"Project not currently connected: {name}")

    connected_sorted = sorted(connected)
    stale_connected = [name for name in connected_sorted if name not in available_names]

    if updated:
        config["connected_projects"] = connected_sorted
        config["projects_confirmed"] = True
        if source_explicit:
            config["source"] = source_choice
        save_config(config)

    payload = {
        "ok": True,
        "updated": updated,
        "source": source_choice,
        "source_selection_confirmed": source_explicit,
        "scope_mode": "connected_only" if connected_sorted else "all_projects",
        "connected_projects": connected_sorted,
        "available_projects": available_names,
        "stale_connected_projects": stale_connected,
        "warnings": warnings,
    }
    print(json.dumps(payload, indent=2))
