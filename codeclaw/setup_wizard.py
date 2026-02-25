"""Compatibility helpers for setup parsing and legacy setup entrypoints."""

from __future__ import annotations

import re
from types import SimpleNamespace

from .config import CodeClawConfig, load_config

_HF_DATASET_URL_RE = re.compile(r"^https?://huggingface\.co/datasets/([^/]+/[^/\s?#]+)/*$", re.IGNORECASE)


def _parse_dataset_repo(raw: str) -> str | None:
    """Parse 'username/dataset' or HF dataset URL into a repo id."""
    value = raw.strip()
    if not value:
        return None

    match = _HF_DATASET_URL_RE.match(value)
    if match:
        return match.group(1).rstrip("/")

    if value.count("/") == 1 and not value.startswith("/") and not value.endswith("/"):
        return value
    return None


def run_wizard(force: bool = False) -> CodeClawConfig:
    """Run setup interactively and return the updated config.

    This delegates to the CLI setup handler so behavior stays aligned.
    """
    from .cli.setup import handle_setup

    args = SimpleNamespace(
        yes=False,
        source="auto",
        repo=None,
        private=None,
        connect_projects=None,
        install_mcp=False,
        start_watch=False,
    )
    if force:
        # force=True keeps interactive behavior but ignores previously saved defaults.
        args.source = "both"
    handle_setup(args)
    return load_config()


def ensure_setup() -> CodeClawConfig:
    """Return existing setup config or run setup when essentials are missing."""
    config = load_config()
    if config.get("source") and config.get("repo"):
        return config
    return run_wizard()
