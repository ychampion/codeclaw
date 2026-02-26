"""CLI entry point for CodeClaw — dispatches to subcommand modules."""

import argparse
import json
import sys
from pathlib import Path

from .. import __version__
from ._helpers import (
    CONFIRM_COMMAND_EXAMPLE,
    EXPORT_REVIEW_PUBLISH_STEPS,
    EXPLICIT_SOURCE_CHOICES,
    SOURCE_CHOICES,
    MIN_MANUAL_SCAN_SESSIONS,
)
from .config import _handle_config
from .console import handle_console
from .diff import handle_diff
from .export import (
    _run_export,
    confirm,
    list_projects,
    prep,
    status,
)
from .growth import handle_doctor, handle_share, handle_stats
from .mcp import handle_install_mcp, handle_serve
from .finetune import handle_finetune
from .projects import handle_projects
from .reset import handle_reset
from .setup import handle_setup
from .tui import handle_tui
from .update import _handle_synthesize, update_skill
from .watch import _handle_watch

# Re-export everything that was previously importable from codeclaw.cli
# to maintain backwards compatibility
from ._helpers import (  # noqa: F811
    CONFIRM_COMMAND_SKIP_FULL_NAME_EXAMPLE,
    HF_TAG,
    MIN_ATTESTATION_CHARS,
    REPO_URL,
    SETUP_TO_PUBLISH_STEPS,
    SKILL_URL,
    REQUIRED_REVIEW_ATTESTATIONS,
    _build_status_next_steps,
    _compute_stage,
    _filter_projects_by_source,
    _format_size,
    _format_token_count,
    _has_session_sources,
    _is_explicit_source_choice,
    _mask_config_for_display,
    _mask_secret,
    _normalize_attestation_text,
    _normalize_source_filter,
    _parse_csv_arg,
    _resolve_source_choice,
    _source_label,
    default_repo_name,
    get_hf_username,
    normalize_repo_id,
)
from .config import (  # noqa: F811
    _get_disabled_projects,
    _is_dataset_globally_enabled,
    _merge_config_list,
    _remove_from_config_list,
    configure,
)
from .export import (  # noqa: F811
    _build_dataset_card,
    _build_pii_commands,
    _collect_review_attestations,
    _extract_manual_scan_sessions,
    _find_export_file,
    _list_project_configs,
    _print_pii_guidance,
    _record_export_metrics,
    _read_sessions_from_jsonl,
    _safe_project_name,
    _scan_for_text_occurrences,
    _scan_high_entropy_strings,
    _scan_pii,
    _validate_publish_attestation,
    export_to_jsonl,
    push_to_huggingface,
)


def _handle_prep(args: argparse.Namespace) -> None:
    prep(source_filter=args.source)


def _handle_status(args: argparse.Namespace) -> None:
    status()


def _handle_confirm(args: argparse.Namespace) -> None:
    if (
        args.attest_asked_full_name
        or args.attest_asked_sensitive
        or args.attest_asked_manual_scan
        or args.attest_manual_scan == "__DEPRECATED_FLAG__"
    ):
        print(json.dumps({
            "error": "Deprecated boolean attestation flags were provided.",
            "hint": (
                "Use text attestations instead so the command can validate what was reviewed."
            ),
            "blocked_on_step": "Step 2/3",
            "process_steps": EXPORT_REVIEW_PUBLISH_STEPS,
            "next_command": CONFIRM_COMMAND_EXAMPLE,
        }, indent=2))
        sys.exit(1)
    confirm(
        file_path=args.file,
        full_name=args.full_name,
        attest_asked_full_name=args.attest_full_name,
        attest_asked_sensitive=args.attest_sensitive,
        attest_manual_scan=args.attest_manual_scan,
        skip_full_name_scan=args.skip_full_name_scan,
    )


def _handle_update_skill(args: argparse.Namespace) -> None:
    update_skill(args.target)


def _handle_list(args: argparse.Namespace) -> None:
    from .export import load_config as _load_config
    config = _load_config()
    resolved_source_choice, _ = _resolve_source_choice(args.source, config)
    list_projects(source_filter=resolved_source_choice)


def _handle_diff(args: argparse.Namespace) -> None:
    handle_diff(args)


def _handle_serve(args: argparse.Namespace) -> None:
    handle_serve()


def _handle_install_mcp(args: argparse.Namespace) -> None:
    handle_install_mcp()


def _handle_finetune(args: argparse.Namespace) -> None:
    handle_finetune(args)


def _resolve_default_command(args: argparse.Namespace) -> str:
    """Resolve the implicit command when no subcommand was provided."""
    if args.command:
        return args.command
    export_intent = bool(
        args.output
        or args.repo
        or args.all_projects
        or args.no_thinking
        or args.no_push
        or args.dry_run
        or args.publish_attestation
        or getattr(args, "attest_user_approved_publish", False)
    )
    return "export" if export_intent else "tui"


def main() -> None:
    COMMAND_HANDLERS = {
        "prep": _handle_prep,
        "status": _handle_status,
        "confirm": _handle_confirm,
        "diff": _handle_diff,
        "update-skill": _handle_update_skill,
        "synthesize": _handle_synthesize,
        "serve": _handle_serve,
        "install-mcp": _handle_install_mcp,
        "watch": _handle_watch,
        "setup": handle_setup,
        "projects": handle_projects,
        "reset": handle_reset,
        "list": _handle_list,
        "config": _handle_config,
        "doctor": handle_doctor,
        "stats": handle_stats,
        "share": handle_share,
        "finetune": _handle_finetune,
        "console": handle_console,
        "tui": handle_tui,
        "export": _run_export,
    }

    parser = argparse.ArgumentParser(description="CodeClaw — Claude/Codex -> Hugging Face")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    prep_parser = sub.add_parser("prep", help="Data prep — discover projects, detect HF, output JSON")
    prep_parser.add_argument("--source", choices=SOURCE_CHOICES, default="auto")
    sub.add_parser("status", help="Show current stage and next steps (JSON)")
    cf = sub.add_parser("confirm", help="Scan for PII, summarize export, and unlock pushing (JSON)")
    cf.add_argument("--file", "-f", type=Path, default=None, help="Path to export JSONL file")
    cf.add_argument("--full-name", type=str, default=None,
                    help="User's full name to scan for in the export file (exact-name privacy check).")
    cf.add_argument("--skip-full-name-scan", action="store_true",
                    help="Skip exact full-name scan when the user declines sharing their name.")
    cf.add_argument("--attest-full-name", type=str, default=None,
                    help="Text attestation describing how full-name scan was done.")
    cf.add_argument("--attest-sensitive", type=str, default=None,
                    help="Text attestation describing sensitive-entity review and outcome.")
    cf.add_argument("--attest-manual-scan", type=str, nargs="?", const="__DEPRECATED_FLAG__", default=None,
                    help=f"Text attestation describing manual scan ({MIN_MANUAL_SCAN_SESSIONS}+ sessions).")
    # Deprecated boolean attestations retained only for a guided migration error.
    cf.add_argument("--attest-asked-full-name", action="store_true", help=argparse.SUPPRESS)
    cf.add_argument("--attest-asked-sensitive", action="store_true", help=argparse.SUPPRESS)
    cf.add_argument("--attest-asked-manual-scan", action="store_true", help=argparse.SUPPRESS)
    list_parser = sub.add_parser("list", help="List all projects")
    list_parser.add_argument("--source", choices=SOURCE_CHOICES, default="auto")
    diff = sub.add_parser("diff", help="Preview exactly what would be redacted")
    diff.add_argument("--source", choices=SOURCE_CHOICES, default="auto")
    diff.add_argument("--all-projects", action="store_true")
    diff.add_argument("--limit", type=int, default=100)
    diff.add_argument("--format", choices=["json", "text"], default="json")

    us = sub.add_parser("update-skill", help="Install/update the codeclaw skill for a coding agent")
    us.add_argument("target", choices=["claude"], help="Agent to install skill for")

    synth = sub.add_parser("synthesize", help="Write CODECLAW.md for a project from synced sessions")
    synth.add_argument("--project", "-p", type=str, required=True, help="Project name to synthesize")
    synth.add_argument("--output", "-o", type=Path, default=None,
                       help="Directory to write CODECLAW.md (default: project root or cwd)")

    watch = sub.add_parser("watch", help="Manage background watcher daemon")
    watch_group = watch.add_mutually_exclusive_group(required=True)
    watch_group.add_argument("--start", action="store_true", help="Start daemon")
    watch_group.add_argument("--stop", action="store_true", help="Stop daemon")
    watch_group.add_argument("--status", action="store_true", help="Show daemon status")
    watch_group.add_argument("--now", action="store_true", help="Trigger immediate sync")
    watch_group.add_argument("--pause", action="store_true", help="Pause polling without stopping daemon")
    watch_group.add_argument("--resume", action="store_true", help="Resume polling after pause")
    watch_group.add_argument("--logs", action="store_true", help="Show daemon logs")
    watch_group.add_argument("--monitor", action="store_true", help="Show live daemon status and recent logs")
    watch_group.add_argument("--switch-project", type=str, default=None, help="Switch connected scope to one project")
    watch_group.add_argument(
        "--set-projects",
        type=str,
        default=None,
        help="Set connected projects via CSV (or use 'all' to clear scope filter)",
    )
    watch.add_argument("--source", choices=SOURCE_CHOICES, default="auto", help="Source scope for project actions")
    watch.add_argument("--lines", type=int, default=80, help="Line count for --logs/--monitor")
    watch.add_argument("--follow", action="store_true", help="Keep streaming logs/monitor output")
    watch.add_argument("--interval", type=float, default=1.5, help="Refresh interval seconds for follow mode")

    setup = sub.add_parser("setup", help="Run setup wizard")
    setup.add_argument("--yes", action="store_true", help="Accept setup defaults")
    setup.add_argument("--source", choices=SOURCE_CHOICES, default="auto")
    setup.add_argument("--repo", type=str, default=None, help="Dataset repo ID or Hugging Face dataset URL")
    visibility = setup.add_mutually_exclusive_group()
    visibility.add_argument("--private", dest="private", action="store_true", help="Use a private dataset repo")
    visibility.add_argument("--public", dest="private", action="store_false", help="Use a public dataset repo")
    setup.set_defaults(private=None)
    setup.add_argument(
        "--connect-projects",
        type=str,
        default=None,
        help="Comma-separated project names to connect during setup",
    )
    setup.add_argument("--install-mcp", action="store_true", help="Install MCP server during setup")
    setup.add_argument("--start-watch", action="store_true", help="Start watcher daemon during setup")

    projects_cmd = sub.add_parser("projects", help="Manage connected project scope")
    projects_cmd.add_argument("--source", choices=SOURCE_CHOICES, default="auto")
    projects_cmd.add_argument("--connect", type=str, default=None, help="Comma-separated project names to connect")
    projects_cmd.add_argument(
        "--disconnect",
        type=str,
        default=None,
        help="Comma-separated project names to disconnect",
    )
    projects_cmd.add_argument("--use-current", action="store_true", help="Connect only the current project")
    projects_cmd.add_argument("--all", action="store_true", help="Connect all discovered projects")
    projects_cmd.add_argument(
        "--clear",
        action="store_true",
        help="Clear connected projects (all discovered projects become eligible)",
    )

    reset = sub.add_parser("reset", help="Reset local setup files for clean re-onboarding")
    reset.add_argument("--all", action="store_true", help="Reset config, local state, and MCP entry")
    reset.add_argument("--config", action="store_true", help="Reset only ~/.codeclaw/config.json")
    reset.add_argument("--state", action="store_true", help="Reset watcher/TUI runtime files under ~/.codeclaw")
    reset.add_argument("--mcp", action="store_true", help="Remove only the CodeClaw MCP entry from ~/.claude/mcp.json")
    reset.add_argument("--yes", action="store_true", help="Skip confirmation prompt")

    cfg = sub.add_parser("config", help="View or set config")
    cfg.add_argument("--repo", type=str, help="Set HF repo")
    cfg.add_argument("--source", choices=sorted(EXPLICIT_SOURCE_CHOICES),
                     help="Set export source scope explicitly: claude, codex, or both")
    cfg.add_argument("--exclude", type=str, help="Comma-separated projects to exclude")
    cfg.add_argument("--redact", type=str,
                     help="Comma-separated strings to always redact (API keys, usernames, domains)")
    cfg.add_argument("--redact-usernames", type=str,
                     help="Comma-separated usernames to anonymize (GitHub handles, Discord names)")
    cfg.add_argument("--confirm-projects", action="store_true",
                     help="Mark project selection as confirmed (include all)")
    cfg_toggle = cfg.add_mutually_exclusive_group()
    cfg_toggle.add_argument("--enable", action="store_true",
                            help="Re-enable dataset generation globally")
    cfg_toggle.add_argument("--disable", action="store_true",
                            help="Disable dataset generation globally (can be re-enabled later)")
    cfg.add_argument("--disable-project", type=str,
                     help="Comma-separated project names to disable dataset generation for")
    cfg.add_argument("--enable-project", type=str,
                     help="Comma-separated project names to re-enable dataset generation for")
    cfg.add_argument(
        "--encryption",
        choices=["on", "off", "status"],
        help="Set encryption-at-rest mode (or inspect status).",
    )

    sub.add_parser("serve", help="Run the CodeClaw MCP server over stdio")
    sub.add_parser("install-mcp", help="Install CodeClaw MCP server into Claude mcp.json")
    doctor = sub.add_parser("doctor", help="Check setup health (logs, HF auth, MCP registration)")
    doctor.add_argument("--source", choices=SOURCE_CHOICES, default="auto")
    stats_cmd = sub.add_parser("stats", help="Show usage and export metrics")
    stats_cmd.add_argument("--source", choices=SOURCE_CHOICES, default="auto")
    stats_cmd.add_argument("--skill", action="store_true", help="Include skill growth analytics")
    share = sub.add_parser("share", help="One-command export flow with optional publish")
    share.add_argument("--output", "-o", type=Path, default=None)
    share.add_argument("--repo", "-r", type=str, default=None)
    share.add_argument("--source", choices=SOURCE_CHOICES, default="auto")
    share.add_argument("--all-projects", action="store_true")
    share.add_argument("--no-thinking", action="store_true")
    share.add_argument("--publish", action="store_true", help="Publish exported data to Hugging Face")
    share.add_argument(
        "--publish-attestation",
        type=str,
        default=None,
        help="Required with --publish: text attestation that publishing was explicitly approved.",
    )

    # Preview command for local fine-tune research.
    finetune = sub.add_parser("finetune", help="Experimental fine-tune scaffold (preview)")
    finetune.add_argument("--experimental", action="store_true")
    finetune.add_argument("--dataset", type=str, default=None)
    finetune.add_argument("--output", type=str, default=None)

    console = sub.add_parser("console", help="Interactive slash-command console")
    console.add_argument("--source", choices=SOURCE_CHOICES, default="auto")

    tui = sub.add_parser("tui", help="Full-screen terminal UI with slash commands and plugins")
    tui.add_argument("--source", choices=SOURCE_CHOICES, default="auto")
    tui.add_argument(
        "--plugin-dir",
        action="append",
        default=[],
        help="Additional plugin directory (repeatable)",
    )

    exp = sub.add_parser("export", help="Export and push datasets")
    # Export flags on both the subcommand and root parser so `codeclaw --no-push` works
    for target in (exp, parser):
        target.add_argument("--output", "-o", type=Path, default=None)
        target.add_argument("--repo", "-r", type=str, default=None)
        target.add_argument("--source", choices=SOURCE_CHOICES, default="auto")
        target.add_argument("--all-projects", action="store_true")
        target.add_argument("--no-thinking", action="store_true")
        target.add_argument("--no-push", action="store_true")
        target.add_argument("--dry-run", action="store_true", help="Preview export/publish plan without writing or pushing")
        target.add_argument(
            "--publish-attestation",
            type=str,
            default=None,
            help="Required for push: text attestation that user explicitly approved publishing.",
        )
        target.add_argument("--attest-user-approved-publish", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args()
    command = _resolve_default_command(args)
    handler = COMMAND_HANDLERS.get(command, _run_export)
    handler(args)
