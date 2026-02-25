"""Shared utilities, constants, and formatting helpers used across CLI subcommands."""

import json
import re
import sys
from pathlib import Path

from ..config import CONFIG_FILE, CodeClawConfig, load_config, save_config
from ..parser import CLAUDE_DIR, CODEX_DIR, detect_current_project, discover_projects
from ..source_adapters import iter_external_adapters
from ..secrets import _has_mixed_char_types, _shannon_entropy

HF_TAG = "codeclaw"
REPO_URL = "https://github.com/ychampion/codeclaw"
SKILL_URL = "https://raw.githubusercontent.com/ychampion/codeclaw/main/docs/SKILL.md"

REQUIRED_REVIEW_ATTESTATIONS: dict[str, str] = {
    "asked_full_name": "I asked the user for their full name and scanned for it.",
    "asked_sensitive_entities": "I asked about company/client/internal names and private URLs.",
    "manual_scan_done": "I performed a manual sample scan of exported sessions.",
}
MIN_ATTESTATION_CHARS = 24
MIN_MANUAL_SCAN_SESSIONS = 20

CONFIRM_COMMAND_EXAMPLE = (
    "codeclaw confirm "
    "--full-name \"THEIR FULL NAME\" "
    "--attest-full-name \"Asked for full name and scanned export for THEIR FULL NAME.\" "
    "--attest-sensitive \"Asked about company/client/internal names and private URLs; user response recorded and redactions updated if needed.\" "
    "--attest-manual-scan \"Manually scanned 20 sessions across beginning/middle/end and reviewed findings with the user.\""
)

CONFIRM_COMMAND_SKIP_FULL_NAME_EXAMPLE = (
    "codeclaw confirm "
    "--skip-full-name-scan "
    "--attest-full-name \"User declined to share full name; skipped exact-name scan.\" "
    "--attest-sensitive \"Asked about company/client/internal names and private URLs; user response recorded and redactions updated if needed.\" "
    "--attest-manual-scan \"Manually scanned 20 sessions across beginning/middle/end and reviewed findings with the user.\""
)

EXPORT_REVIEW_PUBLISH_STEPS = [
    "Step 1/3: Export locally only: codeclaw export --no-push --output /tmp/codeclaw_export.jsonl",
    "Step 2/3: Review/redact, then run confirm: codeclaw confirm ...",
    "Step 3/3: After explicit user approval, publish: codeclaw export --publish-attestation \"User explicitly approved publishing to Hugging Face.\"",
]

SETUP_TO_PUBLISH_STEPS = [
    "Step 1/6: Run prep/list to review project scope: codeclaw prep && codeclaw list",
    "Step 2/6: Explicitly choose source scope: codeclaw config --source <claude|codex|both>",
    "Step 3/6: Configure exclusions/redactions and confirm projects: codeclaw config ...",
    "Step 4/6: Export locally only: codeclaw export --no-push --output /tmp/codeclaw_export.jsonl",
    "Step 5/6: Review and confirm: codeclaw confirm ...",
    "Step 6/6: After explicit user approval, publish: codeclaw export --publish-attestation \"User explicitly approved publishing to Hugging Face.\"",
]

EXTERNAL_SOURCE_CHOICES = {
    "cursor",
    "windsurf",
    "aider",
    "continue",
    "antigravity",
    "vscode",
    "zed",
    "xcode-beta",
}
EXPLICIT_SOURCE_CHOICES = {"claude", "codex", "both"}
SOURCE_CHOICES = ["auto", "claude", "codex", "both", *sorted(EXTERNAL_SOURCE_CHOICES)]
_HF_DATASET_URL_RE = re.compile(r"^https?://huggingface\.co/datasets/([^/\s]+/[^/\s?#]+)/*$", re.IGNORECASE)


def _mask_secret(s: str) -> str:
    """Mask a secret string for display, e.g. 'hf_OOgd...oEVH'."""
    if len(s) <= 8:
        return "***"
    return f"{s[:4]}...{s[-4:]}"


def _mask_config_for_display(config: dict) -> dict:
    """Return a copy of config with redact_strings values masked."""
    out = dict(config)
    if out.get("redact_strings"):
        out["redact_strings"] = [_mask_secret(s) for s in out["redact_strings"]]
    return out


def _source_label(source_filter: str) -> str:
    source_filter = _normalize_source_filter(source_filter)
    if source_filter == "claude":
        return "Claude Code"
    if source_filter == "codex":
        return "Codex"
    if source_filter in EXTERNAL_SOURCE_CHOICES:
        return source_filter
    return "Claude Code or Codex"


def _normalize_source_filter(source_filter: str) -> str:
    if source_filter == "both":
        return "auto"
    return source_filter


def _is_explicit_source_choice(source_filter: str | None) -> bool:
    return source_filter in EXPLICIT_SOURCE_CHOICES


def _resolve_source_choice(
    requested_source: str,
    config: CodeClawConfig | None = None,
) -> tuple[str, bool]:
    """Resolve source choice from CLI + config.

    Returns:
      (source_choice, explicit) where source_choice is one of
      "claude" | "codex" | "both" | "auto".
    """
    if _is_explicit_source_choice(requested_source):
        return requested_source, True
    if config:
        configured_source = config.get("source")
        if _is_explicit_source_choice(configured_source):
            return str(configured_source), True
    return "auto", False


def _has_session_sources(source_filter: str = "auto") -> bool:
    source_filter = _normalize_source_filter(source_filter)
    if source_filter == "claude":
        return CLAUDE_DIR.exists()
    if source_filter == "codex":
        return CODEX_DIR.exists()
    if source_filter in EXTERNAL_SOURCE_CHOICES:
        for adapter in iter_external_adapters():
            if adapter.name == source_filter:
                return any(root.exists() for root in adapter.roots)
        return False
    external_has_logs = any(any(root.exists() for root in adapter.roots) for adapter in iter_external_adapters())
    return CLAUDE_DIR.exists() or CODEX_DIR.exists() or external_has_logs


def _filter_projects_by_source(projects: list[dict], source_filter: str) -> list[dict]:
    source_filter = _normalize_source_filter(source_filter)
    if source_filter == "auto":
        return projects
    return [p for p in projects if p.get("source", "claude") == source_filter]


def _format_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}" if unit != "B" else f"{size_bytes} B"
        size_bytes /= 1024
    return f"{size_bytes:.1f} GB"


def _format_token_count(count: int) -> str:
    if count >= 1_000_000_000:
        return f"{count / 1_000_000_000:.1f}B"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.0f}K"
    return str(count)


def get_hf_username() -> str | None:
    """Get the currently logged-in HF username, or None."""
    try:
        from huggingface_hub import HfApi
        return HfApi().whoami()["name"]
    except ImportError:
        return None
    except (OSError, KeyError, ValueError):
        return None


def default_repo_name(hf_username: str) -> str:
    """Standard repo name: {username}/my-personal-codex-data"""
    return f"{hf_username}/my-personal-codex-data"


def _compute_stage(config: CodeClawConfig) -> tuple[str, int, str | None]:
    """Return (stage_name, stage_number, hf_username)."""
    hf_user = get_hf_username()
    if not hf_user:
        return ("auth", 1, None)
    saved = config.get("stage")
    last_export = config.get("last_export")
    if saved == "done" and last_export:
        return ("done", 4, hf_user)
    if saved == "confirmed" and last_export:
        return ("confirmed", 3, hf_user)
    if saved == "review" and last_export:
        return ("review", 3, hf_user)
    return ("configure", 2, hf_user)


def _build_status_next_steps(
    stage: str, config: CodeClawConfig, hf_user: str | None, repo_id: str | None,
) -> tuple[list[str], str | None]:
    """Return (next_steps, next_command) for the given stage."""
    if stage == "auth":
        return (
            [
                "Ask the user for their Hugging Face token. Sign up: https://huggingface.co/join — Create WRITE token: https://huggingface.co/settings/tokens",
                "Run: huggingface-cli login --token <THEIR_TOKEN> (NEVER run bare huggingface-cli login — it hangs)",
                "Run: codeclaw config --redact \"<THEIR_TOKEN>\" (so the token gets redacted from exports)",
                "Run: codeclaw prep (to confirm login and get next steps)",
            ],
            None,
        )

    if stage == "configure":
        projects_confirmed = config.get("projects_confirmed", False)
        configured_source = config.get("source")
        source_confirmed = _is_explicit_source_choice(configured_source)
        list_command = (
            f"codeclaw list --source {configured_source}" if source_confirmed else "codeclaw list"
        )
        steps = []
        if not source_confirmed:
            steps.append(
                "Ask the user to explicitly choose export source scope: Claude Code, Codex, or both. "
                "Then set it: codeclaw config --source <claude|codex|both>. "
                "Do not run export until source scope is explicitly confirmed."
            )
        else:
            steps.append(
                f"Source scope is currently set to '{configured_source}'. "
                "If the user wants a different scope, run: codeclaw config --source <claude|codex|both>."
            )
        if not projects_confirmed:
            steps.append(
                f"Run: {list_command} — then send the FULL project/folder list to the user in your next message "
                "(name, source, sessions, size, excluded), and ask which to EXCLUDE."
            )
            steps.append(
                "Configure project scope: codeclaw config --exclude \"project1,project2\" "
                "or codeclaw config --confirm-projects (to include all listed projects). "
                "Do not run export until this folder review is confirmed."
            )
        steps.extend([
            "Ask about GitHub/Discord usernames to anonymize and sensitive strings to redact. "
            "Configure: codeclaw config --redact-usernames \"handle1\" and codeclaw config --redact \"string1\"",
            "When done configuring, export locally: codeclaw export --no-push --output /tmp/codeclaw_export.jsonl",
        ])
        # next_command is null because user input is needed before exporting
        return (steps, None)

    if stage == "review":
        return (
            [
                "Ask the user for their full name to run an exact-name privacy check against the export. If they decline, you may skip this check with --skip-full-name-scan and include a clear attestation.",
                "Run PII scan commands and review results with the user.",
                "Ask the user: 'Are there any company names, internal project names, client names, private URLs, or other people's names in your conversations that you'd want redacted? Any custom domains or internal tools?' Add anything they mention with codeclaw config --redact.",
                "Do a deep manual scan: sample ~20 sessions from the export (beginning, middle, end) and scan for names, private URLs, company names, credentials in conversation text, and anything else that looks sensitive. Report findings to the user.",
                "If PII found in any of the above, add redactions (codeclaw config --redact) and re-export: codeclaw export --no-push",
                (
                    "Run: "
                    + CONFIRM_COMMAND_EXAMPLE
                    + " — scans for PII, shows project breakdown, and unlocks pushing."
                ),
                "Do NOT push until the user explicitly confirms. Once confirmed, push: codeclaw export --publish-attestation \"User explicitly approved publishing to Hugging Face.\"",
            ],
            "codeclaw confirm",
        )

    if stage == "confirmed":
        return (
            [
                "User has reviewed the export. Ask: 'Ready to publish to Hugging Face?' and push: codeclaw export --publish-attestation \"User explicitly approved publishing to Hugging Face.\"",
            ],
            "codeclaw export",
        )

    # done
    dataset_url = f"https://huggingface.co/datasets/{repo_id}" if repo_id else None
    return (
        [
            f"Done! Dataset is live{f' at {dataset_url}' if dataset_url else ''}. To update later: codeclaw export",
            "To reconfigure: codeclaw prep then codeclaw config",
        ],
        None,
    )


def _normalize_attestation_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split()).strip()
    return " ".join(str(value).split()).strip()


def _parse_csv_arg(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def normalize_repo_id(value: str | None) -> str | None:
    """Normalize a dataset repo argument into 'namespace/name' or return None."""
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None

    match = _HF_DATASET_URL_RE.match(raw)
    if match:
        return match.group(1).rstrip("/")

    repo_id = raw.rstrip("/")
    if repo_id.count("/") != 1:
        return None
    if repo_id.startswith("/") or repo_id.endswith("/"):
        return None
    return repo_id
