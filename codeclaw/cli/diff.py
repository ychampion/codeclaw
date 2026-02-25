"""Preview what would be redacted before confirm/publish."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ..anonymizer import Anonymizer
from ..config import load_config
from ..parser import discover_projects, parse_project_sessions
from ..redactor import RedactionEngine, redact_session_with_findings
from ._helpers import (
    _filter_projects_by_source,
    _has_session_sources,
    _normalize_source_filter,
    _resolve_source_choice,
)
from .config import _get_disabled_projects


def _scope_projects(config: dict[str, Any], source_choice: str, include_all_projects: bool) -> list[dict[str, Any]]:
    source_filter = _normalize_source_filter(source_choice)
    projects = _filter_projects_by_source(discover_projects(), source_filter)
    excluded = set() if include_all_projects else set(config.get("excluded_projects", []))
    disabled = _get_disabled_projects(config)
    connected = set() if include_all_projects else set(config.get("connected_projects", []))

    scoped: list[dict[str, Any]] = []
    for project in projects:
        name = str(project.get("display_name", ""))
        if connected and name not in connected:
            continue
        if name in excluded or name in disabled:
            continue
        scoped.append(project)
    return scoped


def _load_sessions(projects: list[dict[str, Any]], extra_usernames: list[str]) -> list[dict[str, Any]]:
    anonymizer = Anonymizer(extra_usernames=extra_usernames)
    sessions: list[dict[str, Any]] = []
    for project in projects:
        sessions.extend(
            parse_project_sessions(
                project.get("dir_name", ""),
                anonymizer=anonymizer,
                include_thinking=True,
                source=project.get("source", "claude"),
            )
        )
    return sessions


def handle_diff(args) -> None:
    """Show exact redaction preview across selected sessions."""
    config = load_config()
    source_choice, source_explicit = _resolve_source_choice(args.source, config)
    source_filter = _normalize_source_filter(source_choice)
    if not _has_session_sources(source_filter):
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "No local session sources were found.",
                    "source": source_choice,
                },
                indent=2,
            )
        )
        sys.exit(1)

    projects = _scope_projects(config, source_choice, include_all_projects=bool(args.all_projects))
    if not projects:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "No projects matched scope for redaction diff.",
                    "source": source_choice,
                },
                indent=2,
            )
        )
        sys.exit(1)

    sessions = _load_sessions(projects, config.get("redact_usernames", []))
    if args.limit and args.limit > 0:
        sessions = sessions[: args.limit]

    engine = RedactionEngine(
        engine=str(config.get("pii_engine", "auto")),
        model_size=str(config.get("pii_model_size", "small")),
        confidence_threshold=float(config.get("pii_confidence_threshold", 0.55)),
    )
    custom_strings = config.get("redact_strings", [])

    total_redactions = 0
    by_source: dict[str, int] = {}
    by_category: dict[str, int] = {}
    examples: list[dict[str, Any]] = []
    for session in sessions:
        _, count, findings = redact_session_with_findings(
            session,
            custom_strings=custom_strings,
            engine=engine,
        )
        total_redactions += count
        for finding in findings:
            src = str(finding.get("source", "regex"))
            cat = str(finding.get("category", "sensitive"))
            by_source[src] = by_source.get(src, 0) + 1
            by_category[cat] = by_category.get(cat, 0) + 1
            if len(examples) < 50:
                examples.append(
                    {
                        "session_id": session.get("session_id"),
                        "project": session.get("project"),
                        "field": finding.get("field"),
                        "source": src,
                        "category": cat,
                        "score": finding.get("score"),
                        "text": finding.get("text"),
                    }
                )

    payload = {
        "ok": True,
        "source": source_choice,
        "source_selection_confirmed": source_explicit,
        "sessions_scanned": len(sessions),
        "projects_scanned": len(projects),
        "total_redactions_estimated": total_redactions,
        "findings_by_source": by_source,
        "findings_by_category": by_category,
        "examples": examples,
    }
    if args.format == "text":
        print("CodeClaw Redaction Diff")
        print(json.dumps(payload, indent=2))
    else:
        print(json.dumps(payload, indent=2))
