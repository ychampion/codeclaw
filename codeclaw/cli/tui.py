"""CLI bridge for launching the full-screen TUI."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def handle_tui(args) -> None:
    """Launch the full-screen TUI."""
    try:
        from ..tui import run_tui
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "TUI dependencies are unavailable.",
                    "detail": f"{type(exc).__name__}: {exc}",
                    "hint": "Install prompt_toolkit and retry: pip install prompt_toolkit",
                },
                indent=2,
            )
        )
        sys.exit(1)

    plugin_dirs = [Path(path) for path in (args.plugin_dir or [])]
    try:
        run_tui(source=args.source, plugin_dirs=plugin_dirs or None)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "TUI failed to start.",
                    "detail": f"{type(exc).__name__}: {exc}",
                    "hint": (
                        "Try `codeclaw console` for fallback mode, or run in a fully interactive terminal "
                        "(Windows Terminal / PowerShell with TTY / macOS Terminal / Linux shell)."
                    ),
                },
                indent=2,
            )
        )
        sys.exit(1)

