# TUI Integration Plan

## Step 0 Audit Summary

### Current CLI and entrypoints
- Primary entrypoint: `codeclaw/cli/__init__.py` with subcommands for:
  - setup/config/projects/list/status/prep
  - export/confirm/share/diff
  - watch/doctor/stats
  - mcp serve/install
  - `console` (simple slash-command loop, not full-screen)
- `codeclaw/__main__.py` delegates to CLI main.

### Existing runtime systems to reuse
- Config and persistence:
  - `codeclaw/config.py` stores settings in `~/.codeclaw/config.json`.
- Watch/background daemon:
  - `codeclaw/daemon.py` already supports start/stop/status/sync, log file, state file, pause/resume.
- Export/publish flow:
  - `codeclaw/cli/export.py` implements gated export, confirm, publish, Hugging Face upload.
- Diagnostics and stats:
  - `codeclaw/cli/growth.py` provides doctor/stats/share logic.
- MCP:
  - `codeclaw/mcp_server.py` with install and serve paths.

### Existing interactive UI work
- `codeclaw/cli/console.py` exists and supports slash commands in a line-by-line REPL.
- No continuously rendered full-screen TUI with persistent status bar currently exists.

## Framework Choice

### Chosen: `prompt_toolkit` (Python)
- Already available in common Python environments and cross-platform (Windows/macOS/Linux).
- Strong support for:
  - full-screen terminal layouts
  - persistent input line, history, keybindings
  - command completion/autocomplete
  - non-blocking event loop updates via refresh interval/background work
- Allows separation of business logic (command parsing, jobs, plugins) from rendering for easy tests.

## Proposed Module Layout

New package: `codeclaw/tui/`
- `app.py`
  - Full-screen app, layout, event loop, feed rendering, status bar, input wiring.
- `commands.py`
  - Slash command registry, parser, completion surface, handler dispatch.
- `jobs.py`
  - Background job manager with queue, progress, cancellation flags, status snapshots.
- `plugins.py`
  - Plugin discovery/load/reload/enable/disable and command registration bridge.
- `types.py`
  - Shared dataclasses for command specs, runtime status, job info, plugin info, event payloads.
- `__init__.py`
  - `run_tui(...)` entrypoint.

CLI integration:
- New `codeclaw tui` subcommand in `codeclaw/cli/__init__.py`.
- Keep all existing commands unchanged.

## Slash Command Mapping to Existing Features

Core TUI commands:
- `/help`, `/quit`, `/clear`
- `/status`
  - shows config and runtime summary (daemon state, connected project scope, source, pending jobs).
- `/config get <key>`, `/config set <key> <value>`
  - uses existing config read/write.
- `/watch on|off|status|now|pause|resume`
  - mapped to `codeclaw.daemon` APIs.
- `/export ...`
  - calls existing export flow (`_run_export`) in background job.
- `/jobs`, `/cancel <id>`
  - powered by TUI job manager.
- `/plugins list|reload|enable|disable`
  - powered by plugin manager.

Non-slash text:
- appended to activity feed as user input placeholder for future chat-like UX.

## Plugin API Design

Discovery:
- Local repo `plugins/` and user `~/.codeclaw/plugins`.

Manifest:
- `plugin.json` required per plugin directory:
  - `name` (string, required)
  - `version` (string, required)
  - `entrypoint` (string, optional; default `plugin.py`)
  - `description` (string, optional)

Entrypoint contract:
- Python file defines `register(ctx)` function.
- `ctx` exposes:
  - `register_command(name, handler, help_text, aliases=..., usage=...)`
  - `emit(message, level="info")`

Safety:
- plugin load failures are isolated and reported in feed and plugin list.
- failed plugin must not crash TUI.

State:
- enable/disable persisted in config (`disabled_plugins` list).

## Milestone Implementation Plan

### Milestone 1
- Add `codeclaw/tui` skeleton app, fullscreen layout, feed/input/status bar.
- Add `codeclaw tui` command.
- Implement `/help`, `/quit`.
- Add tests for command parsing/registry.

### Milestone 2
- Expand command registry with aliases/help/usage and autocomplete.
- Add `/clear`, `/status`, `/config get|set`, `/watch on|off`.
- Route command errors into feed + status bar.

### Milestone 3
- Add job manager for background non-blocking tasks.
- Add spinner/job count/status updates.
- Add `/jobs` and `/cancel`.

### Milestone 4
- Integrate export/watch/publish flows through existing functions (not shell-out).
- Emit activity feed events for start/progress/success/failure.

### Milestone 5
- Add plugin manager, plugin command registration, and `/plugins` controls.
- Add plugin failure isolation tests.

### Milestone 6
- Polish UX:
  - improved hints, robust exception boundaries, persisted command history.
- Update README with TUI usage and plugin authoring.
