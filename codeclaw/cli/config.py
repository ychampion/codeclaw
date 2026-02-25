"""Config command and all --flag handlers."""

import json
import sys

from ..config import CONFIG_FILE, CodeClawConfig, load_config, save_config

from ._helpers import (
    _mask_config_for_display,
    _parse_csv_arg,
    normalize_repo_id,
)


def _merge_config_list(config: CodeClawConfig, key: str, new_values: list[str]) -> None:
    """Append new_values to a config list (deduplicated, sorted)."""
    existing = set(config.get(key, []))
    existing.update(new_values)
    config[key] = sorted(existing)


def _remove_from_config_list(config: CodeClawConfig, key: str, values: list[str]) -> None:
    """Remove values from a config list."""
    existing = set(config.get(key, []))
    existing.difference_update(values)
    config[key] = sorted(existing)


def _is_dataset_globally_enabled(config: CodeClawConfig) -> bool:
    """Return True if dataset generation is globally enabled (default True)."""
    return bool(config.get("dataset_enabled", True))


def _get_disabled_projects(config: CodeClawConfig) -> set[str]:
    """Return the set of project names for which dataset generation is disabled."""
    return set(config.get("disabled_projects", []))


def configure(
    repo: str | None = None,
    source: str | None = None,
    exclude: list[str] | None = None,
    redact: list[str] | None = None,
    redact_usernames: list[str] | None = None,
    confirm_projects: bool = False,
    dataset_enabled: bool | None = None,
    disable_projects: list[str] | None = None,
    enable_projects: list[str] | None = None,
):
    """Set config values non-interactively. Lists are MERGED (append), not replaced."""
    config = load_config()
    if repo is not None:
        normalized_repo = normalize_repo_id(repo)
        if normalized_repo is None:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "Invalid dataset repo format.",
                        "hint": (
                            "Use username/dataset-name or a URL like "
                            "https://huggingface.co/datasets/username/dataset-name"
                        ),
                        "provided": repo,
                    },
                    indent=2,
                )
            )
            sys.exit(1)
        config["repo"] = normalized_repo
    if source is not None:
        config["source"] = source
    if exclude is not None:
        _merge_config_list(config, "excluded_projects", exclude)
    if redact is not None:
        _merge_config_list(config, "redact_strings", redact)
    if redact_usernames is not None:
        _merge_config_list(config, "redact_usernames", redact_usernames)
    if confirm_projects:
        config["projects_confirmed"] = True
    if dataset_enabled is not None:
        config["dataset_enabled"] = dataset_enabled
    if disable_projects is not None:
        _merge_config_list(config, "disabled_projects", disable_projects)
    if enable_projects is not None:
        _remove_from_config_list(config, "disabled_projects", enable_projects)
    save_config(config)
    print(f"Config saved to {CONFIG_FILE}")
    print(json.dumps(_mask_config_for_display(config), indent=2))


def _handle_config(args) -> None:
    """Handle the config subcommand."""
    dataset_enabled: bool | None = None
    if getattr(args, "enable", False):
        dataset_enabled = True
    elif getattr(args, "disable", False):
        dataset_enabled = False

    has_changes = (
        args.repo
        or args.source
        or args.exclude
        or args.redact
        or args.redact_usernames
        or args.confirm_projects
        or dataset_enabled is not None
        or getattr(args, "disable_project", None)
        or getattr(args, "enable_project", None)
    )
    if not has_changes:
        print(json.dumps(_mask_config_for_display(load_config()), indent=2))
        return
    configure(
        repo=args.repo,
        source=args.source,
        exclude=_parse_csv_arg(args.exclude),
        redact=_parse_csv_arg(args.redact),
        redact_usernames=_parse_csv_arg(args.redact_usernames),
        confirm_projects=args.confirm_projects or bool(args.exclude),
        dataset_enabled=dataset_enabled,
        disable_projects=_parse_csv_arg(getattr(args, "disable_project", None)),
        enable_projects=_parse_csv_arg(getattr(args, "enable_project", None)),
    )
