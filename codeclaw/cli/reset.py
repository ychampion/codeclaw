"""Reset local CodeClaw setup for clean re-onboarding."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

from ..config import CONFIG_FILE

def _prompt_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "[y/N]" if not default else "[Y/n]"
    try:
        raw = input(f"{prompt} {suffix}: ").strip().lower()
    except EOFError:
        return False
    if not raw:
        return default
    return raw in {"y", "yes"}


def _resolve_targets(args) -> dict[str, bool]:
    selected = {
        "config": bool(getattr(args, "config", False)),
        "state": bool(getattr(args, "state", False)),
        "mcp": bool(getattr(args, "mcp", False)),
    }
    if bool(getattr(args, "all", False)) or not any(selected.values()):
        return {"config": True, "state": True, "mcp": True}
    return selected


def _state_paths() -> list[Path]:
    root = Path.home() / ".codeclaw"
    return [
        root / "daemon.pid",
        root / "daemon.log",
        root / "daemon_state.json",
        root / "pending.jsonl",
        root / "archive",
        root / "logs",
        root / "tui_history",
        root / "last_export.jsonl",
        root / "last_confirm.json",
    ]


def _remove_path(path: Path) -> tuple[bool, str | None]:
    if not path.exists():
        return False, None
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True, None
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _remove_codeclaw_mcp_entry(mcp_path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(mcp_path),
        "exists": mcp_path.exists(),
        "updated": False,
        "removed_entry": False,
        "error": None,
    }
    if not mcp_path.exists():
        return result

    try:
        raw = mcp_path.read_text(encoding="utf-8")
    except OSError as exc:
        result["error"] = f"Could not read mcp.json: {exc}"
        return result

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        result["error"] = f"mcp.json is invalid JSON: {exc}"
        return result

    if not isinstance(payload, dict):
        result["error"] = "mcp.json root is not an object."
        return result

    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        return result
    if "codeclaw" not in servers:
        return result

    del servers["codeclaw"]
    payload["mcpServers"] = servers
    try:
        mcp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        result["error"] = f"Could not update mcp.json: {exc}"
        return result

    result["updated"] = True
    result["removed_entry"] = True
    return result


def handle_reset(args) -> None:
    """Reset local setup files so onboarding can be run from a clean slate."""
    targets = _resolve_targets(args)
    if not bool(getattr(args, "yes", False)):
        selected = [name for name, enabled in targets.items() if enabled]
        selection = ", ".join(selected) if selected else "none"
        confirmed = _prompt_yes_no(
            f"This will remove local CodeClaw setup ({selection}) and stop background watcher. Continue?",
            default=False,
        )
        if not confirmed:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "aborted": True,
                        "targets": targets,
                        "message": "Reset cancelled.",
                    },
                    indent=2,
                )
            )
            sys.exit(1)

    daemon_info: dict[str, Any] = {"stop_attempted": True, "status": None, "error": None}
    try:
        from ..daemon import stop_daemon

        daemon_info["status"] = stop_daemon()
    except Exception as exc:  # pragma: no cover - environment specific
        daemon_info["error"] = f"{type(exc).__name__}: {exc}"

    removed: list[str] = []
    missing: list[str] = []
    errors: list[str] = []

    if targets["config"]:
        did_remove, err = _remove_path(CONFIG_FILE)
        if did_remove:
            removed.append(str(CONFIG_FILE))
        elif err:
            errors.append(f"{CONFIG_FILE}: {err}")
        else:
            missing.append(str(CONFIG_FILE))

    if targets["state"]:
        for path in _state_paths():
            did_remove, err = _remove_path(path)
            if did_remove:
                removed.append(str(path))
            elif err:
                errors.append(f"{path}: {err}")
            else:
                missing.append(str(path))

    mcp_result: dict[str, Any] | None = None
    if targets["mcp"]:
        mcp_result = _remove_codeclaw_mcp_entry(Path.home() / ".claude" / "mcp.json")
        if mcp_result.get("error"):
            errors.append(str(mcp_result["error"]))

    payload: dict[str, Any] = {
        "ok": not errors,
        "targets": targets,
        "daemon": daemon_info,
        "removed": sorted(set(removed)),
        "missing": sorted(set(missing)),
        "errors": errors,
        "next_steps": [
            "Run: codeclaw setup",
            "Run: codeclaw doctor",
        ],
    }
    if mcp_result is not None:
        payload["mcp"] = mcp_result

    print(json.dumps(payload, indent=2))
    if errors:
        sys.exit(1)
