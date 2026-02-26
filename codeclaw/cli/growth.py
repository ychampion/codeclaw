"""Growth-focused CLI commands: doctor, stats, and share."""

from __future__ import annotations

import json
import shutil
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import __version__
from ..anonymizer import Anonymizer
from ..classifier import classify_trajectory
from ..config import CONFIG_FILE, CodeClawConfig, load_config, save_config
from ..parser import discover_projects, parse_project_sessions
from ..source_adapters import adapter_diagnostics
from ..storage import encryption_status
from ._helpers import (
    _filter_projects_by_source,
    _format_size,
    _format_token_count,
    _has_session_sources,
    _normalize_source_filter,
    _resolve_source_choice,
    default_repo_name,
    get_hf_username,
    normalize_repo_id,
)
from .config import _get_disabled_projects
from .export import (
    _record_export_metrics,
    _validate_publish_attestation,
    export_to_jsonl,
    push_to_huggingface,
)


def _runtime_diagnostics() -> dict[str, Any]:
    python_path = Path(sys.executable).resolve()
    command_path_raw = shutil.which("codeclaw")
    command_path = Path(command_path_raw).resolve() if command_path_raw else None
    candidate_script_dirs = {python_path.parent}
    scripts_dir = python_path.parent / "Scripts"
    if scripts_dir.exists():
        candidate_script_dirs.add(scripts_dir)

    command_in_python_env = None
    if command_path is not None:
        command_in_python_env = any(
            command_path == directory or directory in command_path.parents
            for directory in candidate_script_dirs
        )

    return {
        "module_version": __version__,
        "python_executable": str(python_path),
        "codeclaw_on_path": str(command_path) if command_path is not None else None,
        "codeclaw_on_path_in_python_env": command_in_python_env,
        "path_hint": (
            "Command resolution may point to a different Python install. "
            "Use `python -m codeclaw --version` and compare with `codeclaw --version`."
            if command_path is not None and command_in_python_env is False
            else None
        ),
    }


def _check_mcp_registration() -> dict[str, Any]:
    mcp_path = Path.home() / ".claude" / "mcp.json"
    if not mcp_path.exists():
        return {
            "ok": False,
            "path": str(mcp_path),
            "code": "missing_config",
            "message": "Claude MCP config was not found.",
        }

    try:
        raw = mcp_path.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "path": str(mcp_path),
            "code": "read_error",
            "message": f"Could not read MCP config: {exc}",
        }

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "path": str(mcp_path),
            "code": "invalid_json",
            "message": f"MCP config is not valid JSON: {exc}",
        }

    if not isinstance(parsed, dict):
        return {
            "ok": False,
            "path": str(mcp_path),
            "code": "invalid_root",
            "message": "MCP config root must be a JSON object.",
        }

    mcp_servers = parsed.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        return {
            "ok": False,
            "path": str(mcp_path),
            "code": "missing_servers",
            "message": "MCP config is missing a valid mcpServers object.",
        }

    codeclaw_server = mcp_servers.get("codeclaw")
    if not isinstance(codeclaw_server, dict):
        return {
            "ok": False,
            "path": str(mcp_path),
            "code": "missing_codeclaw_server",
            "message": "CodeClaw MCP server is not registered.",
        }

    args = codeclaw_server.get("args")
    args_ok = isinstance(args, list) and "-m" in args and "codeclaw.mcp_server" in args
    if not args_ok:
        return {
            "ok": False,
            "path": str(mcp_path),
            "code": "unexpected_server_args",
            "message": "CodeClaw MCP server entry exists but args are not recognized.",
            "server": codeclaw_server,
        }

    return {
        "ok": True,
        "path": str(mcp_path),
        "command": codeclaw_server.get("command"),
        "args": args,
    }


def _iter_sessions(projects: list[dict[str, Any]], extra_usernames: list[str]) -> list[dict[str, Any]]:
    anonymizer = Anonymizer(extra_usernames=extra_usernames)
    sessions: list[dict[str, Any]] = []
    for project in projects:
        sessions.extend(
            parse_project_sessions(
                project.get("dir_name", ""),
                anonymizer=anonymizer,
                include_thinking=False,
                source=project.get("source", "claude"),
            )
        )
    return sessions


def _discover_projects_safe(source_filter: str) -> tuple[list[dict[str, Any]], str | None]:
    try:
        return _filter_projects_by_source(discover_projects(), source_filter), None
    except Exception as exc:  # pragma: no cover - defensive path
        return [], f"{type(exc).__name__}: {exc}"


def _project_scope(
    config: CodeClawConfig,
    source_choice: str,
    include_all_projects: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[str], list[str], str | None]:
    source_filter = _normalize_source_filter(source_choice)
    projects, discovery_error = _discover_projects_safe(source_filter)
    if discovery_error is not None:
        return [], [], [], [], [], discovery_error

    excluded = set() if include_all_projects else set(config.get("excluded_projects", []))
    disabled = _get_disabled_projects(config)
    connected = set() if include_all_projects else set(config.get("connected_projects", []))
    included: list[dict[str, Any]] = []
    excluded_names: list[str] = []
    disabled_names: list[str] = []
    disconnected_names: list[str] = []
    for project in projects:
        name = str(project.get("display_name", ""))
        if connected and name not in connected:
            disconnected_names.append(name)
            continue
        if name in excluded:
            excluded_names.append(name)
            continue
        if name in disabled:
            disabled_names.append(name)
            continue
        included.append(project)
    return (
        projects,
        included,
        sorted(excluded_names),
        sorted(disabled_names),
        sorted(disconnected_names),
        None,
    )


def handle_doctor(args) -> None:
    """Run setup diagnostics for local logs, Hugging Face auth, and MCP wiring."""
    config = load_config()
    source_choice, source_explicit = _resolve_source_choice(args.source, config)
    source_filter = _normalize_source_filter(source_choice)

    has_sources = _has_session_sources(source_filter)
    projects, project_discovery_error = _discover_projects_safe(source_filter) if has_sources else ([], None)
    hf_user = get_hf_username()
    mcp = _check_mcp_registration()
    encryption = encryption_status(config)
    adapters = adapter_diagnostics()
    runtime = _runtime_diagnostics()
    from ..daemon import daemon_status
    watch = daemon_status()
    connected = set(config.get("connected_projects", []))
    connected_available = sorted(
        str(project.get("display_name", "")) for project in projects if str(project.get("display_name", "")) in connected
    )
    stale_connected = sorted(name for name in connected if name and name not in connected_available)

    checks = {
        "config_file": {
            "ok": CONFIG_FILE.exists(),
            "path": str(CONFIG_FILE),
            "message": "Config file found." if CONFIG_FILE.exists() else "Config file is missing (run codeclaw setup).",
        },
        "session_sources": {
            "ok": has_sources,
            "source_filter": source_choice,
            "message": (
                f"Session logs detected for source '{source_choice}'."
                if has_sources
                else f"No session logs found for source '{source_choice}'."
            ),
        },
        "project_discovery": {
            "ok": project_discovery_error is None and len(projects) > 0,
            "project_count": len(projects),
            "connected_count": len(connected_available),
            "stale_connected_projects": stale_connected,
            "error": project_discovery_error,
            "message": (
                f"Discovered {len(projects)} project(s)."
                if project_discovery_error is None and projects
                else "Project discovery failed."
                if project_discovery_error is not None
                else "No projects discovered in the selected source scope."
            ),
        },
        "huggingface_auth": {
            "ok": hf_user is not None,
            "username": hf_user,
            "message": (
                f"Authenticated as {hf_user}."
                if hf_user
                else "Not authenticated with Hugging Face (run huggingface-cli login)."
            ),
        },
        "mcp_registration": mcp,
        "encryption": {
            "ok": bool(encryption.get("enabled")) and bool(encryption.get("crypto_available")),
            "message": (
                "Encryption is configured and crypto backend is available."
                if bool(encryption.get("enabled")) and bool(encryption.get("crypto_available"))
                else "Encryption fallback/disabled: run codeclaw setup to initialize secure key storage."
            ),
            **encryption,
        },
        "watch_daemon": {
            "ok": True,
            "running": bool(watch.get("running")),
            "paused": bool(watch.get("paused")),
            "pending_sessions": int(watch.get("pending_sessions", 0) or 0),
            "pid": watch.get("pid"),
            "log_file": watch.get("log_file"),
            "state_file": watch.get("state_file"),
            "message": (
                "Watcher daemon is running."
                if watch.get("running")
                else "Watcher daemon is not running (start with `codeclaw watch --start`)."
            ),
        },
    }
    ok = all(bool(item.get("ok")) for item in checks.values())

    next_steps: list[str] = []
    if not checks["session_sources"]["ok"]:
        next_steps.append("Ensure Claude Code or Codex logs exist under ~/.claude/projects or ~/.codex/sessions.")
    if not checks["huggingface_auth"]["ok"]:
        next_steps.append("Run: huggingface-cli login --token <HF_WRITE_TOKEN>")
    if not checks["mcp_registration"]["ok"]:
        next_steps.append("Run: codeclaw install-mcp")
    if not checks["encryption"]["ok"]:
        next_steps.append("Run: codeclaw config --encryption on or codeclaw setup to initialize encryption.")
    if stale_connected:
        next_steps.append("Run: codeclaw projects to review connected project scope.")
    if not watch.get("running"):
        next_steps.append("Run: codeclaw watch --start (or keep manual mode without background sync).")
    if not checks["project_discovery"]["ok"] and checks["session_sources"]["ok"]:
        next_steps.append("Run: codeclaw prep to inspect source scope and project detection.")
    if runtime.get("path_hint"):
        next_steps.append("Run: python -m codeclaw --version and compare with `codeclaw --version`.")
        next_steps.append("If versions differ, reinstall with: python -m pip install --upgrade --force-reinstall codeclaw")

    payload = {
        "ok": ok,
        "source": source_choice,
        "source_selection_confirmed": source_explicit,
        "checks": checks,
        "platform_checks": {
            "os": sys.platform,
            "sigusr1_available": hasattr(signal, "SIGUSR1"),
            "adapter_diagnostics": adapters,
        },
        "runtime": runtime,
        "next_steps": next_steps,
    }
    print(json.dumps(payload, indent=2))
    if not ok:
        sys.exit(1)


def handle_stats(args) -> None:
    """Report local session/export metrics so the dataset pipeline feels alive."""
    config = load_config()
    source_choice, source_explicit = _resolve_source_choice(args.source, config)
    projects, included, excluded_names, disabled_names, disconnected_names, scope_error = _project_scope(
        config=config,
        source_choice=source_choice,
        include_all_projects=False,
    )
    if scope_error is not None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "Project discovery failed while computing stats.",
                    "detail": scope_error,
                    "source": source_choice,
                },
                indent=2,
            )
        )
        sys.exit(1)

    sessions = _iter_sessions(included, config.get("redact_usernames", [])) if included else []
    session_ids = {
        str(session.get("session_id"))
        for session in sessions
        if str(session.get("session_id", "")).strip()
    }
    synced_ids = set(config.get("synced_session_ids", []))
    pending_ids = session_ids - synced_ids

    total_input = sum(int(session.get("stats", {}).get("input_tokens", 0) or 0) for session in sessions)
    total_output = sum(int(session.get("stats", {}).get("output_tokens", 0) or 0) for session in sessions)
    synced_input = sum(
        int(session.get("stats", {}).get("input_tokens", 0) or 0)
        for session in sessions
        if str(session.get("session_id", "")) in synced_ids
    )
    synced_output = sum(
        int(session.get("stats", {}).get("output_tokens", 0) or 0)
        for session in sessions
        if str(session.get("session_id", "")) in synced_ids
    )

    lifetime_exports = int(config.get("stats_total_exports", 0) or 0)
    lifetime_publishes = int(config.get("stats_total_publishes", 0) or 0)
    lifetime_exported_sessions = int(config.get("stats_total_exported_sessions", 0) or 0)
    lifetime_redactions = int(config.get("stats_total_redactions", 0) or 0)
    lifetime_input = int(config.get("stats_total_input_tokens", 0) or 0)
    lifetime_output = int(config.get("stats_total_output_tokens", 0) or 0)

    payload = {
        "ok": True,
        "source": source_choice,
        "source_selection_confirmed": source_explicit,
        "scope": {
            "projects_discovered": len(projects),
            "projects_included": len(included),
            "excluded_projects": excluded_names,
            "disabled_projects": disabled_names,
            "disconnected_projects": disconnected_names,
        },
        "summary": {
            "sessions_available": len(session_ids),
            "sessions_captured": len(synced_ids),
            "sessions_pending": len(pending_ids),
            "tokens_saved": lifetime_input + lifetime_output,
            "redactions_made": lifetime_redactions,
        },
        "tokens": {
            "available_input": total_input,
            "available_output": total_output,
            "available_total": total_input + total_output,
            "captured_input": synced_input,
            "captured_output": synced_output,
            "captured_total": synced_input + synced_output,
            "captured_total_human": _format_token_count(synced_input + synced_output),
        },
        "lifetime": {
            "exports": lifetime_exports,
            "publishes": lifetime_publishes,
            "sessions_exported": lifetime_exported_sessions,
            "redactions": lifetime_redactions,
            "input_tokens": lifetime_input,
            "output_tokens": lifetime_output,
        },
        "dataset_versioning": {
            "mode": config.get("dataset_versioning_mode"),
            "latest_version": config.get("dataset_latest_version"),
            "dedupe_entries": len(config.get("published_dedupe_index", {})),
        },
        "last_export": config.get("last_export"),
    }
    if getattr(args, "skill", False):
        payload["skill"] = _build_skill_metrics(sessions)
    print(json.dumps(payload, indent=2))


def _build_skill_metrics(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute simple growth-oriented trajectory analytics."""
    if not sessions:
        return {
            "score": 0.0,
            "trajectory_counts": {},
            "timeline": [],
        }

    weights = {
        "debugging_trace": 2.0,
        "iterative_build": 1.5,
        "refactor": 1.2,
        "correction_loop": 1.0,
        "sft_clean": 0.8,
    }
    trajectory_counts: dict[str, int] = {}
    timeline: dict[str, dict[str, int]] = {}
    weighted = 0.0
    for session in sessions:
        label = str(session.get("trajectory_type") or classify_trajectory(session))
        trajectory_counts[label] = trajectory_counts.get(label, 0) + 1
        weighted += weights.get(label, 1.0)
        start = session.get("start_time")
        if isinstance(start, str) and start:
            month = start[:7]
        else:
            month = datetime.now().strftime("%Y-%m")
        bucket = timeline.setdefault(month, {})
        bucket[label] = bucket.get(label, 0) + 1

    avg_score = round(weighted / max(len(sessions), 1), 3)
    timeline_rows = [
        {"month": month, "trajectory_counts": timeline[month]}
        for month in sorted(timeline.keys())
    ]
    return {
        "score": avg_score,
        "trajectory_counts": trajectory_counts,
        "timeline": timeline_rows,
    }


def handle_share(args) -> None:
    """Fast export command with optional publish for public sharing loops."""
    config = load_config()
    source_choice, source_explicit = _resolve_source_choice(args.source, config)
    source_filter = _normalize_source_filter(source_choice)
    if not _has_session_sources(source_filter):
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "No local session sources were found for the selected scope.",
                    "source": source_choice,
                    "hint": "Use --source <claude|codex|both> or generate local sessions first.",
                },
                indent=2,
            )
        )
        sys.exit(1)

    projects, included, excluded_names, disabled_names, disconnected_names, scope_error = _project_scope(
        config=config,
        source_choice=source_choice,
        include_all_projects=bool(args.all_projects),
    )
    if scope_error is not None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "Project discovery failed.",
                    "detail": scope_error,
                    "source": source_choice,
                },
                indent=2,
            )
        )
        sys.exit(1)
    if not projects:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "No projects found for the selected source scope.",
                    "source": source_choice,
                },
                indent=2,
            )
        )
        sys.exit(1)

    if not included:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "No projects left to export after exclusions/disabled filters.",
                    "excluded_projects": excluded_names,
                    "disabled_projects": disabled_names,
                    "disconnected_projects": disconnected_names,
                    "hint": "Run codeclaw projects or adjust --exclude/--enable-project settings.",
                },
                indent=2,
            )
        )
        sys.exit(1)

    output_path = args.output or Path("codeclaw_share.jsonl")
    anonymizer = Anonymizer(extra_usernames=config.get("redact_usernames", []))
    meta = export_to_jsonl(
        selected_projects=included,
        output_path=output_path,
        anonymizer=anonymizer,
        include_thinking=not args.no_thinking,
        custom_strings=config.get("redact_strings", []),
    )
    file_size = output_path.stat().st_size if output_path.exists() else 0

    repo_input = args.repo if args.repo is not None else config.get("repo")
    repo_id = normalize_repo_id(repo_input) if repo_input else None
    if repo_input and repo_id is None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "Invalid dataset repo format.",
                    "provided": repo_input,
                    "hint": (
                        "Use username/dataset-name or a URL like "
                        "https://huggingface.co/datasets/username/dataset-name"
                    ),
                },
                indent=2,
            )
        )
        sys.exit(1)
    hf_user = get_hf_username()
    if not repo_id and hf_user:
        repo_id = default_repo_name(hf_user)

    publish_attestation = None
    dataset_url = None
    published = False
    if args.publish:
        publish_attestation, publish_error = _validate_publish_attestation(args.publish_attestation)
        if publish_error:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "Missing or invalid publish attestation.",
                        "publish_attestation_error": publish_error,
                        "next_command": (
                            "codeclaw share --publish "
                            "--publish-attestation \"User explicitly approved publishing to Hugging Face on YYYY-MM-DD.\""
                        ),
                    },
                    indent=2,
                )
            )
            sys.exit(1)
        if not repo_id:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "No Hugging Face dataset repo configured.",
                        "hint": "Use --repo username/dataset (or codeclaw config --repo username/dataset) and retry.",
                    },
                    indent=2,
                )
            )
            sys.exit(1)
        push_to_huggingface(output_path, repo_id, meta)
        dataset_url = f"https://huggingface.co/datasets/{repo_id}"
        published = True

    if repo_id:
        config["repo"] = repo_id
    if source_explicit:
        config["source"] = source_choice
    if publish_attestation:
        config["publish_attestation"] = publish_attestation
    config["stage"] = "done" if published else "review"
    _record_export_metrics(
        config=config,
        meta=meta,
        source_choice=source_choice,
        published=published,
        repo_id=repo_id,
        update_totals=True,
    )
    save_config(config)

    payload = {
        "ok": True,
        "published": published,
        "repo": repo_id,
        "dataset_url": dataset_url,
        "dataset_latest_version": config.get("dataset_latest_version"),
        "source": source_choice,
        "source_selection_confirmed": source_explicit,
        "output_file": str(output_path.resolve()),
        "output_size": _format_size(file_size),
        "sessions": meta.get("sessions", 0),
        "projects": meta.get("projects", []),
        "redactions": meta.get("redactions", 0),
        "input_tokens": meta.get("total_input_tokens", 0),
        "output_tokens": meta.get("total_output_tokens", 0),
        "dataset_card_updated": published,
        "next_steps": (
            ["Share complete. Publish succeeded and dataset card was updated."]
            if published
            else [
                "Local share export created.",
                (
                    "To publish: codeclaw share --publish --publish-attestation "
                    "\"User explicitly approved publishing to Hugging Face.\""
                ),
            ]
        ),
    }
    print(json.dumps(payload, indent=2))
