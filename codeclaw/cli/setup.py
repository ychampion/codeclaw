"""Guided onboarding setup flow for CodeClaw."""

from __future__ import annotations

import contextlib
import getpass
import io
import json
import sys

from ..config import CodeClawConfig, load_config, save_config
from ..parser import detect_current_project, discover_projects
from ._helpers import (
    EXPLICIT_SOURCE_CHOICES,
    _filter_projects_by_source,
    _normalize_source_filter,
    _parse_csv_arg,
    _resolve_source_choice,
    default_repo_name,
    get_hf_username,
    normalize_repo_id,
)


def _prompt_yes_no(prompt: str, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"{prompt} {suffix}: ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def _prompt_source(default: str = "both") -> str:
    choices = ["claude", "codex", "both"]
    default = default if default in choices else "both"
    while True:
        raw = input(f"Source scope (claude/codex/both) [{default}]: ").strip().lower()
        if not raw:
            return default
        if raw in choices:
            return raw
        print("Invalid source. Choose one of: claude, codex, both.")


def _prompt_repo(default_repo: str | None) -> str | None:
    prompt = (
        f"Hugging Face dataset repo or URL [{default_repo}]: "
        if default_repo
        else "Hugging Face dataset repo or URL (username/dataset-name): "
    )
    while True:
        raw = input(prompt).strip()
        if not raw:
            return default_repo
        normalized = normalize_repo_id(raw)
        if normalized:
            return normalized
        print(
            "Invalid format. Use username/dataset-name or "
            "https://huggingface.co/datasets/username/dataset-name"
        )


def _attempt_hf_login(token: str) -> tuple[bool, str | None]:
    try:
        from huggingface_hub import login as hf_login
    except ImportError:
        return False, "huggingface_hub is not installed."
    try:
        hf_login(token=token, add_to_git_credential=False)
    except Exception as exc:  # pragma: no cover - network/auth error
        return False, str(exc)
    username = get_hf_username()
    if username:
        return True, f"Authenticated as {username}."
    return False, "Login command ran, but no active Hugging Face session was detected."


def _ensure_dataset_repo(repo_id: str, private: bool) -> tuple[bool, str | None]:
    try:
        from huggingface_hub import HfApi
    except ImportError:
        return False, "huggingface_hub is not installed."
    try:
        HfApi().create_repo(repo_id=repo_id, repo_type="dataset", private=private, exist_ok=True)
        return True, None
    except Exception as exc:  # pragma: no cover - network/provider error
        return False, str(exc)


def _choose_connected_projects(
    available: list[str],
    current_project: str | None,
    assume_yes: bool,
) -> list[str]:
    if not available:
        return []
    if assume_yes:
        if current_project and current_project in available:
            return [current_project]
        return sorted(available)

    print("\nDiscovered projects:")
    for idx, name in enumerate(available, start=1):
        suffix = " (current)" if current_project == name else ""
        print(f"  {idx}. {name}{suffix}")

    while True:
        raw = input(
            "Connected projects (indexes/names comma-separated, 'all', blank=current/all): "
        ).strip()
        if not raw:
            if current_project and current_project in available:
                return [current_project]
            return sorted(available)

        if raw.lower() == "all":
            return sorted(available)

        selected: set[str] = set()
        invalid: list[str] = []
        for token in [part.strip() for part in raw.split(",") if part.strip()]:
            if token.isdigit():
                index = int(token)
                if 1 <= index <= len(available):
                    selected.add(available[index - 1])
                else:
                    invalid.append(token)
            elif token in available:
                selected.add(token)
            else:
                invalid.append(token)

        if selected and not invalid:
            return sorted(selected)
        print(f"Invalid selection: {', '.join(invalid) if invalid else 'none'}. Try again.")


def _safe_discover_projects(source_choice: str) -> tuple[list[dict], str | None]:
    source_filter = _normalize_source_filter(source_choice)
    try:
        projects = _filter_projects_by_source(discover_projects(), source_filter)
    except Exception as exc:  # pragma: no cover - defensive path
        return [], f"{type(exc).__name__}: {exc}"
    return projects, None


def _install_mcp_safely() -> tuple[bool, str | None]:
    try:
        from ..mcp_server import install_mcp
    except Exception as exc:  # pragma: no cover - import path
        return False, f"Could not import MCP installer: {exc}"

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
            install_mcp()
    except SystemExit as exc:
        return False, f"MCP install exited with code {exc.code}."
    except Exception as exc:  # pragma: no cover - unexpected path
        return False, str(exc)
    return True, None


def _build_next_steps(
    hf_username: str | None,
    repo_id: str | None,
    source_choice: str,
) -> list[str]:
    next_steps: list[str] = []
    if not hf_username:
        next_steps.append(
            "Log in to Hugging Face: huggingface-cli login --token <HF_TOKEN> "
            "(create token at https://huggingface.co/settings/tokens)."
        )
    if not repo_id:
        next_steps.append("Set dataset repo: codeclaw config --repo username/dataset-name")
    next_steps.extend(
        [
            f"Confirm project scope: codeclaw projects --source {source_choice}",
            "Run diagnostics: codeclaw doctor",
            "Export locally first: codeclaw export --no-push",
        ]
    )
    return next_steps


def handle_setup(args) -> None:
    """Run guided onboarding for local setup."""
    config: CodeClawConfig = load_config()

    source_choice, source_explicit = _resolve_source_choice(getattr(args, "source", "auto"), config)
    if not source_explicit:
        if args.yes:
            source_choice = "both"
        else:
            configured = config.get("source")
            default_source = configured if configured in EXPLICIT_SOURCE_CHOICES else "both"
            source_choice = _prompt_source(default_source)

    hf_username = get_hf_username()
    hf_login_attempted = False
    hf_login_message = None
    if not hf_username and not args.yes:
        print("Hugging Face login helps create/publish datasets.")
        print("Create token: https://huggingface.co/settings/tokens")
        token = getpass.getpass("Paste Hugging Face token now (blank to skip): ").strip()
        if token:
            hf_login_attempted = True
            ok, detail = _attempt_hf_login(token)
            hf_username = get_hf_username()
            hf_login_message = detail
            if not ok and detail:
                print(f"Login warning: {detail}", file=sys.stderr)

    repo_input = getattr(args, "repo", None)
    repo_from_arg = normalize_repo_id(repo_input) if repo_input is not None else None
    if repo_input is not None and repo_from_arg is None:
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

    existing_repo = normalize_repo_id(config.get("repo"))
    default_repo = repo_from_arg or existing_repo or (default_repo_name(hf_username) if hf_username else None)
    repo_id = default_repo if args.yes else _prompt_repo(default_repo)

    private_arg = getattr(args, "private", None)
    if private_arg is None:
        repo_private = bool(config.get("repo_private", True))
        if not args.yes:
            repo_private = _prompt_yes_no("Keep dataset private?", default=repo_private)
    else:
        repo_private = bool(private_arg)

    dataset_attempted = False
    dataset_ready = False
    dataset_error: str | None = None
    if repo_id and hf_username:
        should_create = False if args.yes else _prompt_yes_no("Create/verify dataset repo now?", default=True)
        if should_create:
            dataset_attempted = True
            dataset_ready, dataset_error = _ensure_dataset_repo(repo_id, repo_private)

    projects, discovery_error = _safe_discover_projects(source_choice)
    available_names = sorted(str(project.get("display_name", "")) for project in projects)
    current = detect_current_project()
    current_name = str(current.get("display_name")) if current else None

    explicit_connected = _parse_csv_arg(getattr(args, "connect_projects", None))
    connected_projects: list[str]
    invalid_connected: list[str] = []
    if explicit_connected is not None:
        connected_projects = sorted(name for name in explicit_connected if name in available_names)
        invalid_connected = sorted(name for name in explicit_connected if name not in available_names)
    else:
        connected_projects = _choose_connected_projects(available_names, current_name, assume_yes=args.yes)

    install_mcp_requested = bool(getattr(args, "install_mcp", False))
    if not args.yes and not install_mcp_requested:
        install_mcp_requested = _prompt_yes_no("Install CodeClaw MCP server?", default=True)

    mcp_installed = False
    mcp_error: str | None = None
    if install_mcp_requested:
        mcp_installed, mcp_error = _install_mcp_safely()

    start_watch_requested = bool(getattr(args, "start_watch", False))
    if not args.yes and not start_watch_requested:
        start_watch_requested = _prompt_yes_no("Start background watcher now?", default=False)
    elif args.yes:
        start_watch_requested = True

    watch_status: dict[str, object] | None = None
    watch_error: str | None = None
    if start_watch_requested:
        try:
            from ..daemon import start_daemon

            watch_status = start_daemon()
        except Exception as exc:  # pragma: no cover - daemon environment specific
            watch_error = str(exc)

    config["source"] = source_choice
    config["repo"] = repo_id
    config["repo_private"] = repo_private
    config["connected_projects"] = connected_projects
    config["projects_confirmed"] = True
    config["stage"] = "configure"
    save_config(config)

    payload: dict[str, object] = {
        "ok": discovery_error is None,
        "source": source_choice,
        "hf": {
            "username": hf_username,
            "login_attempted": hf_login_attempted,
            "message": hf_login_message,
            "token_url": "https://huggingface.co/settings/tokens",
        },
        "repo": {
            "id": repo_id,
            "url": f"https://huggingface.co/datasets/{repo_id}" if repo_id else None,
            "private": repo_private,
            "create_attempted": dataset_attempted,
            "create_ok": dataset_ready,
            "create_error": dataset_error,
        },
        "projects": {
            "available": available_names,
            "connected": connected_projects,
            "invalid_requested": invalid_connected,
            "current_project": current_name,
            "discovery_error": discovery_error,
        },
        "mcp": {
            "install_requested": install_mcp_requested,
            "installed": mcp_installed,
            "error": mcp_error,
        },
        "watch": {
            "start_requested": start_watch_requested,
            "status": watch_status,
            "error": watch_error,
        },
        "next_steps": _build_next_steps(hf_username, repo_id, source_choice),
    }
    if isinstance(watch_status, dict) and "pid" in watch_status:
        payload["pid"] = watch_status["pid"]
    print(json.dumps(payload, indent=2))
