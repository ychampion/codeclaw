"""Background watcher daemon for CodeClaw."""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .anonymizer import Anonymizer
from .cli import export_to_jsonl, push_to_huggingface
from .config import load_config, save_config
from .parser import PROJECTS_DIR, discover_projects
from .storage import maybe_encrypt_file, read_text, write_text

CODECLAW_DIR = Path.home() / ".codeclaw"
PID_FILE = CODECLAW_DIR / "daemon.pid"
LOG_FILE = CODECLAW_DIR / "daemon.log"
STATE_FILE = CODECLAW_DIR / "daemon_state.json"
PENDING_FILE = CODECLAW_DIR / "pending.jsonl"
ARCHIVE_DIR = CODECLAW_DIR / "archive"
SYSTEMD_USER_UNIT = Path.home() / ".config" / "systemd" / "user" / "codeclaw.service"
LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.codeclaw.watch.plist"

RETRY_BACKOFF_SECONDS = (30, 120, 300)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _setup_logger() -> logging.Logger:
    CODECLAW_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("codeclaw.daemon")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def _read_state() -> dict[str, object]:
    if not STATE_FILE.exists():
        return {}
    try:
        raw = STATE_FILE.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _write_state(update: dict[str, object], *, replace: bool = False) -> dict[str, object]:
    CODECLAW_DIR.mkdir(parents=True, exist_ok=True)
    state = {} if replace else _read_state()
    state.update(update)
    state["updated_at"] = _now_iso()
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        return state
    return state


def _is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, SystemError):
        return False


def _read_pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if _is_pid_running(pid) else None


def _parse_iso(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _scan_changed_project_dirs(last_synced_at: str | None) -> set[str]:
    changed: set[str] = set()
    if not PROJECTS_DIR.exists():
        return changed
    threshold = _parse_iso(last_synced_at)
    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for session_file in project_dir.glob("*.jsonl"):
            try:
                if session_file.stat().st_mtime > threshold:
                    changed.add(project_dir.name)
                    break
            except OSError:
                continue
    return changed


def _append_file(src: Path, dst: Path) -> None:
    if not src.exists() or src.stat().st_size == 0:
        return
    CODECLAW_DIR.mkdir(parents=True, exist_ok=True)
    src_text = read_text(src, config=load_config())
    if not src_text.strip():
        return
    existing = read_text(dst, config=load_config()) if dst.exists() else ""
    merged = existing + ("" if existing.endswith("\n") or not existing else "\n") + src_text
    write_text(dst, merged)
    maybe_encrypt_file(dst, config=load_config())


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return sum(1 for line in read_text(path, config=load_config()).splitlines() if line.strip())
    except OSError:
        return 0


def _rotate_pending() -> Path | None:
    if not PENDING_FILE.exists() or PENDING_FILE.stat().st_size == 0:
        return None
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_file = ARCHIVE_DIR / f"{datetime.now(tz=timezone.utc):%Y%m%d}.jsonl"
    pending_text = read_text(PENDING_FILE, config=load_config())
    existing = read_text(archive_file, config=load_config()) if archive_file.exists() else ""
    merged = existing + ("" if existing.endswith("\n") or not existing else "\n") + pending_text
    write_text(archive_file, merged)
    maybe_encrypt_file(archive_file, config=load_config())
    PENDING_FILE.unlink(missing_ok=True)
    return archive_file


def _poll_once(logger: logging.Logger) -> int:
    config = load_config()
    if bool(config.get("watch_paused", False)):
        logger.info("Watcher paused; skipping poll cycle")
        _write_state(
            {
                "running": True,
                "paused": True,
                "last_poll_at": _now_iso(),
                "last_result": "paused",
                "last_sessions": 0,
                "pending_sessions": _count_jsonl(PENDING_FILE),
                "last_error": None,
            }
        )
        return 0

    changed_dirs = _scan_changed_project_dirs(config.get("last_synced_at"))
    if not changed_dirs:
        logger.info("No new session files detected")
        _write_state(
            {
                "running": True,
                "paused": False,
                "last_poll_at": _now_iso(),
                "last_result": "no_changes",
                "last_sessions": 0,
                "pending_sessions": _count_jsonl(PENDING_FILE),
                "last_changed_projects": [],
                "last_error": None,
            }
        )
        return 0

    selected = [p for p in discover_projects() if p.get("dir_name") in changed_dirs]
    if not selected:
        logger.info("No matching projects after change scan")
        config["last_synced_at"] = _now_iso()
        save_config(config)
        _write_state(
            {
                "running": True,
                "paused": False,
                "last_poll_at": _now_iso(),
                "last_result": "no_matching_projects",
                "last_sessions": 0,
                "pending_sessions": _count_jsonl(PENDING_FILE),
                "last_changed_projects": sorted(changed_dirs),
                "last_error": None,
            }
        )
        return 0

    anonymizer = Anonymizer(extra_usernames=config.get("redact_usernames", []))
    with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        meta = export_to_jsonl(
            selected_projects=selected,
            output_path=tmp_path,
            anonymizer=anonymizer,
            include_thinking=True,
            custom_strings=config.get("redact_strings", []),
        )
        new_sessions = meta.get("sessions", 0)
        push_attempted = False
        push_succeeded = False
        if new_sessions:
            _append_file(tmp_path, PENDING_FILE)
            logger.info("Appended %s sessions to pending queue", new_sessions)
        else:
            logger.info("No new sessions after dedupe/classification")

        pending_count = _count_jsonl(PENDING_FILE)
        auto_push = bool(config.get("auto_push", False))
        min_sessions = int(config.get("min_sessions_before_push", 5) or 5)
        if auto_push and pending_count >= min_sessions:
            repo_id = config.get("repo")
            if not repo_id:
                logger.error("auto_push enabled but no repo configured")
            else:
                push_attempted = True
                logger.info("Auto-push triggered: pending=%s threshold=%s", pending_count, min_sessions)
                pushed = False
                for attempt in range(len(RETRY_BACKOFF_SECONDS) + 1):
                    try:
                        push_to_huggingface(PENDING_FILE, repo_id, dict(meta))
                        pushed = True
                        break
                    except SystemExit:
                        if attempt >= len(RETRY_BACKOFF_SECONDS):
                            logger.exception("Auto-push failed after all retries")
                            break
                        delay = RETRY_BACKOFF_SECONDS[attempt]
                        logger.exception(
                            "Auto-push attempt %s failed; retrying in %ss", attempt + 1, delay
                        )
                        time.sleep(delay)
                if pushed:
                    push_succeeded = True
                    archive_file = _rotate_pending()
                    logger.info("Auto-push succeeded; rotated pending to %s", archive_file)
                    _run_synthesizer_for_projects(meta.get("projects", []), logger)
                    _rebuild_graph_index(logger)

        config = load_config()
        config["last_synced_at"] = _now_iso()
        save_config(config)
        _write_state(
            {
                "running": True,
                "paused": False,
                "last_poll_at": _now_iso(),
                "last_result": "sessions_added" if new_sessions else "dedupe_noop",
                "last_sessions": int(new_sessions),
                "pending_sessions": _count_jsonl(PENDING_FILE),
                "last_changed_projects": sorted(changed_dirs),
                "auto_push_attempted": push_attempted,
                "auto_push_succeeded": push_succeeded,
                "last_error": None,
            }
        )
        return new_sessions
    except Exception as exc:
        _write_state(
            {
                "running": True,
                "paused": bool(load_config().get("watch_paused", False)),
                "last_poll_at": _now_iso(),
                "last_result": "error",
                "last_sessions": 0,
                "pending_sessions": _count_jsonl(PENDING_FILE),
                "last_changed_projects": sorted(changed_dirs),
                "last_error": f"{type(exc).__name__}: {exc}",
            }
        )
        raise
    finally:
        tmp_path.unlink(missing_ok=True)


def _rebuild_graph_index(logger: logging.Logger) -> None:
    """Rebuild the graph index from the archive after a successful push."""
    try:
        from codeclaw.graph_index import build_index_from_archive
        index = build_index_from_archive()
        logger.info("Graph index rebuilt: %s", index.stats())
    except Exception:
        logger.exception("Graph index rebuild failed; continuing")


def _run_synthesizer_for_projects(project_names: list[str], logger: logging.Logger) -> None:
    """Run the CODECLAW.md synthesizer for each project after a successful push."""
    try:
        from .synthesizer import synthesize_for_project
    except Exception:
        logger.exception("Could not import synthesizer; skipping CODECLAW.md generation")
        return
    for project in project_names:
        try:
            out_path = synthesize_for_project(project)
            if out_path:
                logger.info("Synthesized CODECLAW.md for %s at %s", project, out_path)
            else:
                logger.info("No sessions found for synthesizer: %s", project)
        except Exception:
            logger.exception("Synthesizer failed for project %s", project)


class _StopState:
    def __init__(self) -> None:
        self.stop_requested = False
        self.trigger_now = threading.Event()


def _install_watch_service() -> None:
    if sys.platform == "darwin":
        LAUNCHD_PLIST.parent.mkdir(parents=True, exist_ok=True)
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.codeclaw.watch</string>
<key>ProgramArguments</key><array>
<string>{sys.executable}</string><string>-m</string><string>codeclaw.daemon</string><string>--run</string>
</array>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>{LOG_FILE}</string>
<key>StandardErrorPath</key><string>{LOG_FILE}</string>
</dict></plist>
"""
        LAUNCHD_PLIST.write_text(plist, encoding="utf-8")
        return

    if sys.platform.startswith("linux"):
        SYSTEMD_USER_UNIT.parent.mkdir(parents=True, exist_ok=True)
        unit = f"""[Unit]
Description=CodeClaw watch daemon

[Service]
ExecStart={sys.executable} -m codeclaw.daemon --run
Restart=always

[Install]
WantedBy=default.target
"""
        SYSTEMD_USER_UNIT.write_text(unit, encoding="utf-8")


def start_daemon() -> dict[str, object]:
    CODECLAW_DIR.mkdir(parents=True, exist_ok=True)
    existing = _read_pid()
    if existing:
        return {"running": True, "pid": existing, "paused": bool(load_config().get("watch_paused", False))}
    proc = subprocess.Popen(
        [sys.executable, "-m", "codeclaw.daemon", "--run"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    _write_state(
        {
            "running": True,
            "pid": proc.pid,
            "started_at": _now_iso(),
            "stopped_at": None,
            "paused": bool(load_config().get("watch_paused", False)),
            "last_error": None,
        },
        replace=True,
    )
    _install_watch_service()
    return {"running": True, "pid": proc.pid}


def stop_daemon() -> dict[str, object]:
    pid = _read_pid()
    if not pid:
        PID_FILE.unlink(missing_ok=True)
        _write_state({"running": False, "pid": None, "stopped_at": _now_iso()})
        return {"running": False}
    os.kill(pid, signal.SIGTERM)
    _write_state({"running": False, "stopping_pid": pid, "stop_requested_at": _now_iso()})
    return {"running": False, "stopping_pid": pid}


def daemon_status() -> dict[str, object]:
    pid = _read_pid()
    config = load_config()
    state = _read_state()
    pending_sessions = _count_jsonl(PENDING_FILE)
    log_size_bytes = LOG_FILE.stat().st_size if LOG_FILE.exists() else 0
    return {
        "running": bool(pid),
        "pid": pid,
        "paused": bool(config.get("watch_paused", False)),
        "source": config.get("source"),
        "connected_projects": config.get("connected_projects", []),
        "pending_sessions": pending_sessions,
        "last_synced_at": config.get("last_synced_at"),
        "pid_file": str(PID_FILE),
        "log_file": str(LOG_FILE),
        "log_size_bytes": log_size_bytes,
        "state_file": str(STATE_FILE),
        "pending_file": str(PENDING_FILE),
        "state": state,
    }


def set_watch_paused(paused: bool) -> dict[str, object]:
    config = load_config()
    config["watch_paused"] = bool(paused)
    save_config(config)
    now = _now_iso()
    _write_state(
        {
            "paused": bool(paused),
            "pause_updated_at": now,
            "paused_at": now if paused else None,
            "resumed_at": now if not paused else None,
            "last_error": None,
        }
    )
    status = daemon_status()
    status["ok"] = True
    status["action"] = "paused" if paused else "resumed"
    return status


def read_recent_logs(lines: int = 80) -> list[str]:
    if lines <= 0:
        return []
    if not LOG_FILE.exists():
        return []
    try:
        content = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return content[-lines:]


def trigger_sync_now() -> dict[str, object]:
    logger = _setup_logger()
    pid = _read_pid()
    if pid:
        if hasattr(signal, "SIGUSR1"):
            try:
                os.kill(pid, signal.SIGUSR1)
                return {"triggered": True, "pid": pid, "mode": "signal"}
            except OSError:
                sessions = _poll_once(logger)
                return {"triggered": True, "standalone": True, "sessions": sessions, "mode": "signal_fallback"}
        # Windows does not expose SIGUSR1; run one local sync cycle instead.
        sessions = _poll_once(logger)
        return {
            "triggered": True,
            "standalone": True,
            "sessions": sessions,
            "running_pid": pid,
            "mode": "standalone_fallback",
        }
    sessions = _poll_once(logger)
    return {"triggered": True, "standalone": True, "sessions": sessions}


def run_daemon() -> None:
    logger = _setup_logger()
    state = _StopState()
    _write_state(
        {
            "running": True,
            "pid": os.getpid(),
            "started_at": _read_state().get("started_at", _now_iso()),
            "stopped_at": None,
            "last_error": None,
            "paused": bool(load_config().get("watch_paused", False)),
        }
    )

    def _request_stop(signum, _frame):
        logger.info("Received signal %s; will stop after current poll cycle", signum)
        state.stop_requested = True
        state.trigger_now.set()

    def _request_now(_signum, _frame):
        logger.info("Received SIGUSR1; triggering immediate sync")
        state.trigger_now.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    if hasattr(signal, "SIGUSR1"):
        signal.signal(signal.SIGUSR1, _request_now)

    observer = None
    watch_enabled = False
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        class _WatchHandler(FileSystemEventHandler):
            def on_created(self, _event):
                state.trigger_now.set()

            def on_modified(self, _event):
                state.trigger_now.set()

        if PROJECTS_DIR.exists():
            observer = Observer()
            observer.schedule(_WatchHandler(), str(PROJECTS_DIR), recursive=True)
            observer.daemon = True
            observer.start()
            watch_enabled = True
            logger.info("Using watchdog filesystem monitor on %s", PROJECTS_DIR)
    except Exception:
        logger.info("watchdog unavailable, falling back to polling")

    try:
        if watch_enabled:
            try:
                _poll_once(logger)
            except Exception:
                logger.exception("Initial poll cycle failed; continuing daemon loop")
        while True:
            if state.stop_requested:
                break

            interval = int(load_config().get("watch_interval_seconds", 60) or 60)
            if watch_enabled:
                state.trigger_now.wait()
                state.trigger_now.clear()
                if state.stop_requested:
                    break
                try:
                    _poll_once(logger)
                except Exception:
                    logger.exception("Poll cycle failed; continuing daemon loop")
            else:
                time.sleep(interval)
                if state.stop_requested:
                    break
                try:
                    _poll_once(logger)
                except Exception:
                    logger.exception("Poll cycle failed; continuing daemon loop")
    finally:
        if observer is not None:
            observer.stop()
            observer.join(timeout=5)
        config = load_config()
        config["last_synced_at"] = _now_iso()
        save_config(config)
        PID_FILE.unlink(missing_ok=True)
        _write_state({"running": False, "pid": None, "stopped_at": _now_iso()})
        logger.info("Daemon exited cleanly")


def main() -> None:
    parser = argparse.ArgumentParser(description="CodeClaw watch daemon runner")
    parser.add_argument("--run", action="store_true", help="Run daemon loop")
    args = parser.parse_args()
    if args.run:
        run_daemon()


if __name__ == "__main__":
    main()
