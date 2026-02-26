"""Persistent config for CodeClaw, stored at ~/.codeclaw/config.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TypedDict

CONFIG_DIR = Path.home() / ".codeclaw"
CONFIG_FILE = CONFIG_DIR / "config.json"


class CodeClawConfig(TypedDict, total=False):
    """Expected shape of the config dict."""

    repo: str | None
    repo_private: bool
    source: str | None  # "claude" | "codex" | "both"
    connected_projects: list[str]
    encryption_enabled: bool
    encryption_key_ref: str | None
    pii_engine: str  # "regex" | "ml" | "auto"
    pii_model_size: str  # "small" | "large"
    pii_confidence_threshold: float
    adapter_tiers: dict[str, str]
    router_strategy: str
    dataset_versioning_mode: str
    dataset_latest_version: str | None
    published_dedupe_index: dict[str, dict[str, str]]
    excluded_projects: list[str]
    redact_strings: list[str]
    redact_usernames: list[str]
    last_export: dict
    stage: str | None  # "auth" | "configure" | "review" | "confirmed" | "done"
    projects_confirmed: bool
    watch_interval_seconds: int
    watch_paused: bool
    min_sessions_before_push: int
    auto_push: bool
    last_synced_at: str | None
    synced_session_ids: list[str]
    publish_attestation: str | None
    dataset_enabled: bool
    disabled_projects: list[str]
    stats_total_exports: int
    stats_total_publishes: int
    stats_total_exported_sessions: int
    stats_total_redactions: int
    stats_total_input_tokens: int
    stats_total_output_tokens: int


DEFAULT_CONFIG: CodeClawConfig = {
    "repo": None,
    "repo_private": True,
    "source": None,
    "connected_projects": [],
    "encryption_enabled": True,
    "encryption_key_ref": None,
    "pii_engine": "auto",
    "pii_model_size": "small",
    "pii_confidence_threshold": 0.55,
    "adapter_tiers": {},
    "router_strategy": "intelligent_fallback",
    "dataset_versioning_mode": "immutable_snapshots",
    "dataset_latest_version": None,
    "published_dedupe_index": {},
    "excluded_projects": [],
    "redact_strings": [],
    "redact_usernames": [],
    "projects_confirmed": False,
    "synced_session_ids": [],
    "dataset_enabled": True,
    "disabled_projects": [],
    "watch_interval_seconds": 60,
    "watch_paused": False,
    "min_sessions_before_push": 5,
    "auto_push": False,
    "stats_total_exports": 0,
    "stats_total_publishes": 0,
    "stats_total_exported_sessions": 0,
    "stats_total_redactions": 0,
    "stats_total_input_tokens": 0,
    "stats_total_output_tokens": 0,
}


def load_config() -> CodeClawConfig:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8", errors="replace") as f:
                stored = json.load(f)
            return {**DEFAULT_CONFIG, **stored}
        except (json.JSONDecodeError, OSError) as exc:
            print(f"Warning: could not read {CONFIG_FILE}: {exc}", file=sys.stderr)
    return dict(DEFAULT_CONFIG)


def save_config(config: CodeClawConfig) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        CONFIG_FILE.chmod(0o600)
    except OSError as exc:
        print(f"Warning: could not save {CONFIG_FILE}: {exc}", file=sys.stderr)
