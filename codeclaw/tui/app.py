"""Prompt-toolkit based full-screen TUI for CodeClaw."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import logging.handlers
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea

from ..cli.export import _run_export
from ..config import load_config, save_config
from ..daemon import daemon_status, set_watch_paused, start_daemon, stop_daemon, trigger_sync_now
from ..parser import detect_current_project
from .commands import CommandRegistry
from .jobs import JobContext, JobManager
from .plugins import PluginManager
from .types import CommandResult, SlashCommand


class SlashCommandCompleter(Completer):
    """Autocomplete for slash-command names."""

    def __init__(self, registry: CommandRegistry) -> None:
        self.registry = registry

    def get_completions(self, document, complete_event):  # pragma: no cover - thin wrapper
        text = document.text_before_cursor
        for token in self.registry.completions(text):
            start = -len(text.strip()) if text.strip() else 0
            yield Completion(token, start_position=start)


class CodeClawTuiApp:
    """Main full-screen TUI application."""

    SPINNER_FRAMES = ("|", "/", "-", "\\")

    def __init__(self, source: str = "auto", plugin_dirs: list[Path] | None = None) -> None:
        self.source = source
        self.mode = "idle"
        self.last_result = "ready"
        self.last_result_level = "info"
        self.hint = "/help for commands"
        self.spinner_index = 0
        self.feed_lines: list[str] = []
        self.logger = self._setup_logger()
        self._watch_running = False
        self._watch_paused = False
        self._last_watch_probe = 0.0

        self.registry = CommandRegistry()
        self.jobs = JobManager(max_workers=2)
        self.plugin_manager = PluginManager(
            registry=self.registry,
            emit=self.emit_feed,
            plugin_dirs=plugin_dirs or PluginManager.default_plugin_dirs(),
        )

        self.feed = TextArea(
            text="",
            read_only=True,
            scrollbar=True,
            focusable=False,
            wrap_lines=False,
        )
        history_path = Path.home() / ".codeclaw" / "tui_history"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        self.input = TextArea(
            height=1,
            prompt="> ",
            multiline=False,
            completer=SlashCommandCompleter(self.registry),
            history=FileHistory(str(history_path)),
            wrap_lines=False,
        )
        self.input.accept_handler = self._accept_input
        self.status_control = FormattedTextControl(self._status_bar_fragments)

        root = HSplit(
            [
                self.feed,
                self.input,
                Window(content=self.status_control, height=1, style="class:statusbar"),
            ]
        )
        self.layout = Layout(root, focused_element=self.input)
        self.bindings = self._build_keybindings()
        self.style = Style.from_dict({"statusbar": "reverse"})
        self.app = Application(
            layout=self.layout,
            key_bindings=self.bindings,
            full_screen=True,
            mouse_support=True,
            refresh_interval=0.2,
            style=self.style,
        )

        self._register_builtin_commands()
        self.plugin_manager.reload()
        self._show_banner()

    def _setup_logger(self) -> logging.Logger:
        log_dir = Path.home() / ".codeclaw" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("codeclaw.tui")
        logger.setLevel(logging.INFO)
        if logger.handlers:
            return logger
        handler = logging.handlers.RotatingFileHandler(
            log_dir / "tui.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        return logger

    def _build_keybindings(self) -> KeyBindings:
        bindings = KeyBindings()

        @bindings.add("c-c")
        def _exit(_event) -> None:
            self.app.exit()

        return bindings

    def _show_banner(self) -> None:
        logo_lines = [
            "  ____          _      ____ _                 ",
            " / ___|___   __| | ___/ ___| | __ ___      __ ",
            "| |   / _ \\ / _` |/ _ \\___ \\ |/ _` \\ \\ /\\ / / ",
            "| |__| (_) | (_| |  __/___) | | (_| |\\ V  V /  ",
            " \\____\\___/ \\__,_|\\___|____/|_|\\__,_| \\_/\\_/   ",
        ]
        for line in logo_lines:
            self.emit_feed(line, level="info")
        self.emit_feed("CodeClaw TUI ready", level="success")
        self.emit_feed(f"platform={sys.platform} source={self.source}", level="info")
        self.emit_feed("Try: /help  /status  /watch status  /jobs", level="info")

    def _refresh_feed_text(self) -> None:
        self.feed.text = "\n".join(self.feed_lines[-1000:])

    def emit_feed(self, message: str, level: str = "info") -> None:
        prefix = {
            "error": "[error]",
            "warning": "[warn]",
            "success": "[ok]",
            "info": "[info]",
        }.get(level, "[info]")
        self.feed_lines.append(f"{prefix} {message}")
        self._refresh_feed_text()
        payload = {
            "ts": time.time(),
            "level": level,
            "message": message,
            "mode": self._runtime_mode(),
            "source": self.source,
        }
        with contextlib.suppress(Exception):
            self.logger.info(json.dumps(payload, ensure_ascii=True))

    def _set_result(self, message: str, level: str = "info") -> None:
        self.last_result = message
        self.last_result_level = level

    def _status_bar_fragments(self) -> FormattedText:
        self._drain_job_events()
        self._probe_watch_state()
        self.mode = self._runtime_mode()
        spinner = self.SPINNER_FRAMES[self.spinner_index % len(self.SPINNER_FRAMES)] if self.jobs.active_count() else " "
        self.spinner_index += 1
        current_project = self._current_project_label()
        jobs = self.jobs.active_count()
        text = (
            f" CodeClaw {spinner} mode={self.mode} project={current_project} "
            f"| jobs={jobs} | last={self.last_result} | {self.hint} "
        )
        return FormattedText([("", text)])

    def _current_project_label(self) -> str:
        cfg = load_config()
        connected = cfg.get("connected_projects", [])
        if connected:
            if len(connected) == 1:
                return str(connected[0])
            return f"{connected[0]}+{len(connected) - 1}"
        current = detect_current_project()
        if current and current.get("display_name"):
            return str(current["display_name"])
        return "all"

    def _runtime_mode(self) -> str:
        if self.jobs.active_count():
            active_names = {job.name for job in self.jobs.list_jobs() if job.status in {"queued", "running"}}
            if "export" in active_names:
                return "exporting"
            return "running"
        if self._watch_running and self._watch_paused:
            return "paused"
        if self._watch_running:
            return "watching"
        return "idle"

    def _probe_watch_state(self) -> None:
        now = time.time()
        if now - self._last_watch_probe < 1.0:
            return
        self._last_watch_probe = now
        with contextlib.suppress(Exception):
            payload = daemon_status()
            self._watch_running = bool(payload.get("running"))
            self._watch_paused = bool(payload.get("paused"))

    def _accept_input(self, buffer) -> bool:
        line = buffer.text.strip()
        buffer.text = ""
        if not line:
            return False
        self.handle_input(line)
        return False

    def handle_input(self, line: str) -> None:
        if not line.startswith("/"):
            self.emit_feed(f"user> {line}", level="info")
            self._set_result("text accepted", level="info")
            return

        try:
            parsed = self.registry.parse(line)
            if parsed is None:
                self._set_result("invalid command", level="error")
                self.emit_feed("Invalid command. Use /help.", level="error")
                return
            command = self.registry.get(parsed.command_name)
            if command is None:
                self._set_result("unknown command", level="error")
                self.emit_feed(f"Unknown command: /{parsed.command_name}", level="error")
                return
            result = command.handler(self, parsed.args)
            if result.message:
                self.emit_feed(result.message, level="success" if result.ok else "error")
            self._set_result(
                result.message or ("ok" if result.ok else "error"),
                level="info" if result.ok else "error",
            )
        except Exception as exc:
            self.emit_feed(f"Command error: {type(exc).__name__}: {exc}", level="error")
            self._set_result("command error", level="error")

    def _register_builtin_commands(self) -> None:
        self.registry.register(
            SlashCommand(
                name="help",
                aliases=("h", "?"),
                help_text="Show slash command help",
                usage="/help",
                handler=self._cmd_help,
            )
        )
        self.registry.register(
            SlashCommand(
                name="quit",
                aliases=("exit", "q"),
                help_text="Exit TUI",
                usage="/quit",
                handler=self._cmd_quit,
            )
        )
        self.registry.register(
            SlashCommand(
                name="clear",
                help_text="Clear activity feed",
                usage="/clear",
                handler=self._cmd_clear,
            )
        )
        self.registry.register(
            SlashCommand(
                name="status",
                help_text="Show runtime and config status",
                usage="/status",
                handler=self._cmd_status,
            )
        )
        self.registry.register(
            SlashCommand(
                name="config",
                help_text="Get or set config values",
                usage="/config get <key> | /config set <key> <json_or_text>",
                handler=self._cmd_config,
            )
        )
        self.registry.register(
            SlashCommand(
                name="watch",
                help_text="Control watcher daemon",
                usage="/watch on|off|status|now|pause|resume",
                handler=self._cmd_watch,
            )
        )
        self.registry.register(
            SlashCommand(
                name="jobs",
                help_text="List active and completed background jobs",
                usage="/jobs",
                handler=self._cmd_jobs,
            )
        )
        self.registry.register(
            SlashCommand(
                name="cancel",
                help_text="Cancel a background job (best effort)",
                usage="/cancel <job_id>",
                handler=self._cmd_cancel,
            )
        )
        self.registry.register(
            SlashCommand(
                name="export",
                help_text="Run export flow in background",
                usage="/export [--push] [--source ...] [--output ...] [--dry-run]",
                handler=self._cmd_export,
            )
        )
        self.registry.register(
            SlashCommand(
                name="plugins",
                help_text="Manage TUI plugins",
                usage="/plugins list|reload|enable <name>|disable <name>",
                handler=self._cmd_plugins,
            )
        )

    def _cmd_help(self, _ctx, _args: list[str]) -> CommandResult:
        lines = ["Available commands:"]
        for name, help_text, usage in self.registry.help_rows():
            if usage:
                lines.append(f"  {name}: {help_text} | usage: {usage}")
            else:
                lines.append(f"  {name}: {help_text}")
        self.emit_feed("\n".join(lines), level="info")
        return CommandResult(ok=True, message="help displayed")

    def _cmd_quit(self, _ctx, _args: list[str]) -> CommandResult:
        self.app.exit()
        return CommandResult(ok=True, message="Exiting...")

    def _cmd_clear(self, _ctx, _args: list[str]) -> CommandResult:
        self.feed_lines = []
        self._refresh_feed_text()
        return CommandResult(ok=True, message="feed cleared")

    def _cmd_status(self, _ctx, _args: list[str]) -> CommandResult:
        cfg = load_config()
        payload = {
            "mode": self._runtime_mode(),
            "source": cfg.get("source"),
            "connected_projects": cfg.get("connected_projects", []),
            "watch": daemon_status(),
            "jobs_active": self.jobs.active_count(),
        }
        self.emit_feed(json.dumps(payload, indent=2), level="info")
        return CommandResult(ok=True, message="status shown", data=payload)

    def _cmd_config(self, _ctx, args: list[str]) -> CommandResult:
        if not args:
            return CommandResult(False, "Usage: /config get <key> | /config set <key> <value>")
        action = args[0].lower()
        cfg = load_config()
        if action == "get":
            if len(args) != 2:
                return CommandResult(False, "Usage: /config get <key>")
            key = args[1]
            value = cfg.get(key)
            self.emit_feed(json.dumps({key: value}, indent=2), level="info")
            return CommandResult(True, f"config[{key}] shown")
        if action == "set":
            if len(args) < 3:
                return CommandResult(False, "Usage: /config set <key> <value>")
            key = args[1]
            raw_value = " ".join(args[2:])
            with contextlib.suppress(json.JSONDecodeError):
                cfg[key] = json.loads(raw_value)
                save_config(cfg)
                return CommandResult(True, f"config[{key}] updated")
            cfg[key] = raw_value
            save_config(cfg)
            return CommandResult(True, f"config[{key}] updated")
        return CommandResult(False, "Unknown config action. Use get or set.")

    def _cmd_watch(self, _ctx, args: list[str]) -> CommandResult:
        if not args:
            return CommandResult(False, "Usage: /watch on|off|status|now|pause|resume")
        action = args[0].lower()
        if action in {"on", "start"}:
            payload = start_daemon()
            self.emit_feed(json.dumps(payload, indent=2), level="info")
            self._watch_running = bool(payload.get("running"))
            self._watch_paused = bool(payload.get("paused", False))
            ok = bool(payload.get("running"))
            return CommandResult(ok, "watcher started" if ok else "watcher start failed")
        if action in {"off", "stop"}:
            payload = stop_daemon()
            self.emit_feed(json.dumps(payload, indent=2), level="info")
            self._watch_running = bool(payload.get("running"))
            self._watch_paused = False
            ok = payload.get("running") is False
            return CommandResult(ok, "watcher stopped" if ok else "watcher stop failed")
        if action == "status":
            payload = daemon_status()
            self.emit_feed(json.dumps(payload, indent=2), level="info")
            self._watch_running = bool(payload.get("running"))
            self._watch_paused = bool(payload.get("paused", False))
            return CommandResult(True, "watch status shown")
        if action in {"now", "sync"}:
            payload = trigger_sync_now()
            self.emit_feed(json.dumps(payload, indent=2), level="info")
            return CommandResult(bool(payload.get("triggered")), "sync triggered")
        if action == "pause":
            payload = set_watch_paused(True)
            self.emit_feed(json.dumps(payload, indent=2), level="info")
            self._watch_running = bool(payload.get("running", self._watch_running))
            self._watch_paused = bool(payload.get("paused", True))
            return CommandResult(bool(payload.get("ok", True)), "watch paused")
        if action == "resume":
            payload = set_watch_paused(False)
            self.emit_feed(json.dumps(payload, indent=2), level="info")
            self._watch_running = bool(payload.get("running", self._watch_running))
            self._watch_paused = bool(payload.get("paused", False))
            return CommandResult(bool(payload.get("ok", True)), "watch resumed")
        return CommandResult(False, f"Unknown watch action: {action}")

    def _cmd_jobs(self, _ctx, _args: list[str]) -> CommandResult:
        jobs = [job.__dict__ for job in self.jobs.list_jobs()]
        payload = {
            "active": self.jobs.active_count(),
            "completed": sum(1 for job in jobs if job.get("status") in {"success", "error", "cancelled"}),
            "jobs": jobs,
        }
        self.emit_feed(json.dumps(payload, indent=2), level="info")
        return CommandResult(True, "jobs listed")

    def _cmd_cancel(self, _ctx, args: list[str]) -> CommandResult:
        if len(args) != 1:
            return CommandResult(False, "Usage: /cancel <job_id>")
        ok = self.jobs.cancel(args[0])
        if ok:
            return CommandResult(True, f"cancel requested for job {args[0]}")
        return CommandResult(False, f"unable to cancel job {args[0]}")

    def _cmd_export(self, _ctx, args: list[str]) -> CommandResult:
        parser = argparse.ArgumentParser(prog="/export", add_help=False)
        parser.add_argument("--push", action="store_true")
        parser.add_argument("--all-projects", action="store_true")
        parser.add_argument("--no-thinking", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--source", default=self.source)
        parser.add_argument("--repo", default=None)
        parser.add_argument("--output", default=None)
        parser.add_argument("--publish-attestation", default=None)
        try:
            parsed = parser.parse_args(args)
        except SystemExit:
            return CommandResult(False, "Usage: /export [--push] [--source ...] [--output ...] [--dry-run]")

        namespace = SimpleNamespace(
            output=Path(parsed.output) if parsed.output else None,
            repo=parsed.repo,
            source=parsed.source,
            all_projects=parsed.all_projects,
            no_thinking=parsed.no_thinking,
            no_push=not parsed.push,
            dry_run=parsed.dry_run,
            publish_attestation=parsed.publish_attestation,
            attest_user_approved_publish=False,
        )

        def _job_fn(job_ctx: JobContext) -> dict[str, Any]:
            job_ctx.progress(0.05, "starting export")
            stdout = io.StringIO()
            stderr = io.StringIO()
            exit_code = 0
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                try:
                    _run_export(namespace)
                except SystemExit as exc:
                    try:
                        exit_code = int(exc.code)
                    except Exception:
                        exit_code = 1
            job_ctx.progress(1.0, "export completed")
            return {"stdout": stdout.getvalue(), "stderr": stderr.getvalue(), "exit_code": exit_code}

        job = self.jobs.submit(name="export", fn=_job_fn)
        return CommandResult(True, f"export job queued: {job.id}")

    def _cmd_plugins(self, _ctx, args: list[str]) -> CommandResult:
        if not args:
            args = ["list"]
        action = args[0].lower()
        if action == "list":
            payload = [
                {
                    "name": record.name,
                    "version": record.version,
                    "enabled": record.enabled,
                    "loaded": record.loaded,
                    "commands": sorted(record.registered_commands),
                    "error": record.error,
                }
                for record in self.plugin_manager.list_records()
            ]
            self.emit_feed(json.dumps(payload, indent=2), level="info")
            return CommandResult(True, "plugins listed")
        if action == "reload":
            records = self.plugin_manager.reload()
            return CommandResult(True, f"plugins reloaded ({len(records)} discovered)")
        if action == "enable" and len(args) == 2:
            ok = self.plugin_manager.enable(args[1])
            if ok:
                return CommandResult(True, f"plugin enabled: {args[1]}")
            return CommandResult(False, f"plugin enable failed: {args[1]}")
        if action == "disable" and len(args) == 2:
            ok = self.plugin_manager.disable(args[1])
            if ok:
                return CommandResult(True, f"plugin disabled: {args[1]}")
            return CommandResult(False, f"plugin disable failed: {args[1]}")
        return CommandResult(False, "Usage: /plugins list|reload|enable <name>|disable <name>")

    def _drain_job_events(self) -> None:
        max_output = 4000
        for event in self.jobs.poll_events():
            if event.kind == "progress":
                pct = int((event.progress or 0) * 100)
                self.emit_feed(f"job {event.job_id} progress: {pct}% {event.message}", level="info")
                continue
            if event.kind == "started":
                self.emit_feed(f"job {event.job_id} started", level="info")
                continue
            if event.kind == "success":
                result = event.payload.get("result") if isinstance(event.payload, dict) else None
                self.emit_feed(f"job {event.job_id} completed", level="success")
                if isinstance(result, dict):
                    stdout = str(result.get("stdout", "")).strip()
                    stderr = str(result.get("stderr", "")).strip()
                    exit_code = int(result.get("exit_code", 0) or 0)
                    if stdout:
                        if len(stdout) > max_output:
                            stdout = f"{stdout[:max_output]}\n... output truncated ..."
                        self.emit_feed(stdout, level="info")
                    if stderr:
                        if len(stderr) > max_output:
                            stderr = f"{stderr[:max_output]}\n... output truncated ..."
                        self.emit_feed(stderr, level="warning")
                    if exit_code != 0:
                        self.emit_feed(f"job {event.job_id} exited with code {exit_code}", level="error")
                continue
            if event.kind in {"error", "cancelled", "cancelling"}:
                level = "error" if event.kind == "error" else "warning"
                self.emit_feed(f"job {event.job_id} {event.kind}: {event.message}", level=level)
                continue

    def run(self) -> None:
        try:
            self.app.run()
        finally:
            self.jobs.shutdown()


def run_tui(source: str = "auto", plugin_dirs: list[Path] | None = None) -> None:
    """Run the fullscreen CodeClaw TUI."""
    app = CodeClawTuiApp(source=source, plugin_dirs=plugin_dirs)
    app.run()

