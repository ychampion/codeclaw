"""Export-related commands: prep, export, confirm, list, and status."""

import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ..anonymizer import Anonymizer
from ..classifier import classify_trajectory
from ..config import CONFIG_FILE, CodeClawConfig, load_config, save_config
from ..parser import CLAUDE_DIR, CODEX_DIR, detect_current_project, discover_projects, parse_project_sessions
from ..secrets import _has_mixed_char_types, _shannon_entropy, redact_session

from ._helpers import (
    CONFIRM_COMMAND_EXAMPLE,
    CONFIRM_COMMAND_SKIP_FULL_NAME_EXAMPLE,
    EXPORT_REVIEW_PUBLISH_STEPS,
    HF_TAG,
    MIN_ATTESTATION_CHARS,
    MIN_MANUAL_SCAN_SESSIONS,
    REPO_URL,
    SETUP_TO_PUBLISH_STEPS,
    EXPLICIT_SOURCE_CHOICES,
    SOURCE_CHOICES,
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
    _resolve_source_choice,
    _source_label,
    default_repo_name,
    get_hf_username,
    normalize_repo_id,
)
from .config import _get_disabled_projects, _is_dataset_globally_enabled


def list_projects(source_filter: str = "auto") -> None:
    """Print all projects as JSON (for agents to parse)."""
    projects = _filter_projects_by_source(discover_projects(), source_filter)
    if not projects:
        print(f"No {_source_label(source_filter)} sessions found.")
        return
    config = load_config()
    excluded = set(config.get("excluded_projects", []))
    connected = set(config.get("connected_projects", []))
    current = detect_current_project()
    current_name = current["display_name"] if current else None
    print(json.dumps(
        [{"name": p["display_name"], "sessions": p["session_count"],
          "size": _format_size(p["total_size_bytes"]),
          "excluded": p["display_name"] in excluded,
          "connected": p["display_name"] in connected if connected else True,
          "current": p["display_name"] == current_name,
          "source": p.get("source", "claude")}
         for p in projects],
        indent=2,
    ))


def export_to_jsonl(
    selected_projects: list[dict],
    output_path: Path,
    anonymizer: Anonymizer,
    include_thinking: bool = True,
    custom_strings: list[str] | None = None,
) -> dict:
    """Export selected projects to JSONL. Returns metadata."""
    config = load_config()
    synced_session_ids = set(config.get("synced_session_ids", []))
    total = 0
    skipped = 0
    total_redactions = 0
    models: dict[str, int] = {}
    trajectory_types: dict[str, int] = {}
    total_input_tokens = 0
    total_output_tokens = 0
    project_names = []
    exported_session_ids: list[str] = []
    sessions_by_project: dict[str, list[dict]] = defaultdict(list)

    try:
        fh = open(output_path, "w", encoding="utf-8")
    except OSError as e:
        print(f"Error: cannot write to {output_path}: {e}", file=sys.stderr)
        sys.exit(1)

    with fh as f:
        for project in selected_projects:
            print(f"  Parsing {project['display_name']}...", end="", flush=True)
            sessions = parse_project_sessions(
                project["dir_name"], anonymizer=anonymizer,
                include_thinking=include_thinking,
                source=project.get("source", "claude"),
            )
            proj_count = 0
            for session in sessions:
                model = session.get("model")
                if not model or model == "<synthetic>":
                    skipped += 1
                    continue

                session, n_redacted = redact_session(session, custom_strings=custom_strings)
                total_redactions += n_redacted
                session_id = str(session.get("session_id", ""))
                if session_id in synced_session_ids:
                    skipped += 1
                    continue
                trajectory_type = classify_trajectory(session)
                session["trajectory_type"] = trajectory_type

                f.write(json.dumps(session, ensure_ascii=False) + "\n")
                total += 1
                proj_count += 1
                if session_id:
                    exported_session_ids.append(session_id)
                project_name = str(session.get("project", project["display_name"]))
                sessions_by_project[project_name].append(session)
                models[model] = models.get(model, 0) + 1
                trajectory_types[trajectory_type] = trajectory_types.get(trajectory_type, 0) + 1
                stats = session.get("stats", {})
                total_input_tokens += stats.get("input_tokens", 0)
                total_output_tokens += stats.get("output_tokens", 0)
            if proj_count:
                project_names.append(project["display_name"])
            print(f" {proj_count} sessions")

    return {
        "sessions": total,
        "skipped": skipped,
        "redactions": total_redactions,
        "models": models,
        "trajectory_types": trajectory_types,
        "projects": project_names,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "exported_at": datetime.now(tz=timezone.utc).isoformat(),
        "exported_session_ids": exported_session_ids,
        "sessions_by_project": dict(sessions_by_project),
    }


def _safe_project_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(name or "unknown").strip().lower()).strip("-")
    return safe or "unknown"


def _list_project_configs(files: list[str]) -> list[str]:
    projects = sorted({
        parts[1]
        for path in files
        if path.startswith("data/")
        for parts in [path.split("/")]
        if len(parts) >= 3
    })
    return projects


def _read_sessions_from_jsonl(jsonl_path: Path) -> list[dict]:
    sessions: list[dict] = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            sessions.append(json.loads(line))
    return sessions


def push_to_huggingface(
    jsonl_path: Path,
    repo_id: str,
    meta: dict,
    private: bool | None = None,
) -> None:
    """Push JSONL + metadata to HF dataset repo."""
    try:
        from huggingface_hub import HfApi
    except ImportError:
        print("Error: huggingface_hub not installed. Run: pip install huggingface_hub", file=sys.stderr)
        sys.exit(1)

    api = HfApi()
    private_repo = bool(load_config().get("repo_private", True)) if private is None else bool(private)

    try:
        user_info = api.whoami()
        print(f"Logged in as: {user_info['name']}")
    except (OSError, KeyError, ValueError) as e:
        print(f"Error: Not logged in to Hugging Face ({e}).", file=sys.stderr)
        print("Run: huggingface-cli login", file=sys.stderr)
        sys.exit(1)

    print(f"Pushing to: {repo_id}")
    try:
        api.create_repo(repo_id, repo_type="dataset", private=private_repo, exist_ok=True)

        sessions = _read_sessions_from_jsonl(jsonl_path)
        uploaded_projects: set[str] = set()
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
        for session in sessions:
            project = _safe_project_name(str(session.get("project", "unknown")))
            uploaded_projects.add(project)
            session_id = str(session.get("session_id", "unknown"))[:8] or "unknown"
            path_in_repo = f"data/{project}/train-{ts}-{session_id}.jsonl"
            api.upload_file(
                path_or_fileobj=(json.dumps(session, ensure_ascii=False) + "\n").encode(),
                path_in_repo=path_in_repo,
                repo_id=repo_id, repo_type="dataset",
                commit_message=f"Add session {session_id} to {project}",
            )

        api.upload_file(
            path_or_fileobj=json.dumps(meta, indent=2).encode(),
            path_in_repo="metadata.json",
            repo_id=repo_id, repo_type="dataset",
            commit_message="Update metadata",
        )

        repo_files = api.list_repo_files(repo_id=repo_id, repo_type="dataset")
        project_configs = _list_project_configs(repo_files)

        api.upload_file(
            path_or_fileobj=_build_dataset_card(repo_id, meta, project_configs).encode(),
            path_in_repo="README.md",
            repo_id=repo_id, repo_type="dataset",
            commit_message="Update dataset card",
        )
    except (OSError, ValueError) as e:
        print(f"Error uploading to Hugging Face: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error reading {jsonl_path}: {e}", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    synced = set(config.get("synced_session_ids", []))
    synced.update(meta.get("exported_session_ids", []))
    config["synced_session_ids"] = sorted(synced)
    config["last_synced_at"] = datetime.now(tz=timezone.utc).isoformat()
    save_config(config)

    print(f"\nDataset: https://huggingface.co/datasets/{repo_id}")
    print(f"Browse all: https://huggingface.co/datasets?other={HF_TAG}")


def _record_export_metrics(
    config: CodeClawConfig,
    meta: dict[str, object],
    source_choice: str,
    published: bool,
    repo_id: str | None = None,
    update_totals: bool = True,
) -> None:
    """Persist last-export metadata and lifetime counters in config."""
    sessions = int(meta.get("sessions", 0) or 0)
    redactions = int(meta.get("redactions", 0) or 0)
    input_tokens = int(meta.get("total_input_tokens", 0) or 0)
    output_tokens = int(meta.get("total_output_tokens", 0) or 0)

    config["last_export"] = {
        "timestamp": meta.get("exported_at"),
        "sessions": sessions,
        "models": meta.get("models", {}),
        "projects": meta.get("projects", []),
        "source": source_choice,
        "redactions": redactions,
        "total_input_tokens": input_tokens,
        "total_output_tokens": output_tokens,
        "published": published,
        "repo": repo_id,
    }

    if update_totals:
        config["stats_total_exports"] = int(config.get("stats_total_exports", 0) or 0) + 1
        config["stats_total_exported_sessions"] = (
            int(config.get("stats_total_exported_sessions", 0) or 0) + sessions
        )
        config["stats_total_redactions"] = int(config.get("stats_total_redactions", 0) or 0) + redactions
        config["stats_total_input_tokens"] = (
            int(config.get("stats_total_input_tokens", 0) or 0) + input_tokens
        )
        config["stats_total_output_tokens"] = (
            int(config.get("stats_total_output_tokens", 0) or 0) + output_tokens
        )

    if published:
        config["stats_total_publishes"] = int(config.get("stats_total_publishes", 0) or 0) + 1


def _build_dataset_card(repo_id: str, meta: dict, project_configs: list[str] | None = None) -> str:
    models = meta.get("models", {})
    sessions = meta.get("sessions", 0)
    projects = meta.get("projects", [])
    trajectory_types = meta.get("trajectory_types", {})
    total_input = meta.get("total_input_tokens", 0)
    total_output = meta.get("total_output_tokens", 0)
    timestamp = meta.get("exported_at", "")[:10]
    project_configs = project_configs or []

    model_tags = "\n".join(f"  - {m}" for m in sorted(models.keys()) if m != "unknown")
    model_lines = "\n".join(
        f"| {m} | {c} |" for m, c in sorted(models.items(), key=lambda x: -x[1])
    )
    trajectory_lines = "\n".join(
        f"| {name} | {count} |" for name, count in sorted(trajectory_types.items(), key=lambda x: -x[1])
    ) or "| sft_clean | 0 |"
    configs_yaml = ""
    if project_configs:
        entries = "\n".join(
            f"  - config_name: {project}\n    data_files: data/{project}/*.jsonl"
            for project in project_configs
        )
        configs_yaml = f"configs:\n{entries}\n"

    return f"""---
license: mit
task_categories:
  - text-generation
language:
  - en
tags:
  - codeclaw
  - claude-code
  - codex-cli
  - conversations
  - coding-assistant
  - tool-use
  - agentic-coding
{model_tags}
{configs_yaml}
pretty_name: Coding Agent Conversations
---

# Coding Agent Conversation Logs

> **This is a performance art project.** Anthropic built their models on the world's freely shared information, then introduced increasingly [dystopian data policies](https://www.anthropic.com/news/detecting-and-preventing-distillation-attacks) to stop anyone else from doing the same — pulling up the ladder behind them. CodeClaw lets you throw the ladder back down. The dataset it produces is yours to share.

Exported with [CodeClaw]({REPO_URL}).

**Tag: `codeclaw`** — [Browse all CodeClaw datasets](https://huggingface.co/datasets?other=codeclaw)

## Stats

| Metric | Value |
|--------|-------|
| Sessions | {sessions} |
| Projects | {len(projects)} |
| Input tokens | {_format_token_count(total_input)} |
| Output tokens | {_format_token_count(total_output)} |
| Last updated | {timestamp} |

### Models

| Model | Sessions |
|-------|----------|
{model_lines}

### Trajectory Types

| Type | Sessions |
|------|----------|
{trajectory_lines}

## Schema

Each line in `data/<project>/train-*.jsonl` is one conversation session:

```json
{{
  "session_id": "uuid",
  "project": "my-project",
  "model": "gpt-5.3-codex",
  "git_branch": "main",
  "start_time": "2025-01-15T10:00:00+00:00",
  "end_time": "2025-01-15T10:30:00+00:00",
  "trajectory_type": "debugging_trace",
  "messages": [
    {{"role": "user", "content": "Fix the login bug", "timestamp": "..."}},
    {{
      "role": "assistant",
      "content": "I'll investigate the login flow.",
      "thinking": "The user wants me to...",
      "tool_uses": [{{"tool": "Read", "input": "src/auth.py"}}],
      "timestamp": "..."
    }}
  ],
  "stats": {{
    "user_messages": 5,
    "assistant_messages": 8,
    "tool_uses": 20,
    "input_tokens": 50000,
    "output_tokens": 3000
  }}
}}
```

### Privacy

- Paths anonymized to project-relative; usernames hashed
- No tool outputs — only tool call inputs (summaries)

## Load

```python
from datasets import load_dataset
ds = load_dataset("{repo_id}", split="train")
```

## Export your own

```bash
pip install codeclaw
codeclaw
```
"""


def status() -> None:
    """Show current stage and next steps (JSON). Read-only — does not modify config."""
    config = load_config()
    stage, stage_number, hf_user = _compute_stage(config)

    repo_id = config.get("repo")
    if not repo_id and hf_user:
        repo_id = default_repo_name(hf_user)

    next_steps, next_command = _build_status_next_steps(stage, config, hf_user, repo_id)

    current = detect_current_project()
    result = {
        "stage": stage,
        "stage_number": stage_number,
        "total_stages": 4,
        "current_project": current["display_name"] if current else None,
        "hf_logged_in": hf_user is not None,
        "hf_username": hf_user,
        "repo": repo_id,
        "source": config.get("source"),
        "projects_confirmed": config.get("projects_confirmed", False),
        "last_export": config.get("last_export"),
        "next_steps": next_steps,
        "next_command": next_command,
    }
    print(json.dumps(result, indent=2))


def _find_export_file(file_path: Path | None) -> Path:
    """Resolve the export file path, or exit with an error."""
    if file_path and file_path.exists():
        return file_path
    if file_path is None:
        for c in [Path("/tmp/codeclaw_export.jsonl"), Path("codeclaw_conversations.jsonl")]:
            if c.exists():
                return c
    print(json.dumps({
        "error": "No export file found.",
        "hint": "Run step 1 first to generate a local export file.",
        "blocked_on_step": "Step 1/3",
        "process_steps": EXPORT_REVIEW_PUBLISH_STEPS,
        "next_command": "codeclaw export --no-push --output /tmp/codeclaw_export.jsonl",
    }, indent=2))
    sys.exit(1)


def _scan_high_entropy_strings(content: str, max_results: int = 15) -> list[dict]:
    """Scan for high-entropy random strings that might be leaked secrets.

    Complements the regex-based _scan_pii by catching unquoted tokens
    that slipped through Layer 1 (secrets.py) redaction.
    """
    if not content:
        return []

    _CANDIDATE_RE = re.compile(r'[A-Za-z0-9_/+=.-]{20,}')

    # Prefixes already caught by other scans
    _KNOWN_PREFIXES = ("eyJ", "ghp_", "gho_", "ghs_", "ghr_", "sk-", "hf_",
                       "AKIA", "pypi-", "npm_", "xox")

    # Benign prefixes that look random but aren't secrets
    _BENIGN_PREFIXES = ("https://", "http://", "sha256-", "sha384-", "sha512-",
                        "sha1-", "data:", "file://", "mailto:")

    # Substrings that indicate non-secret content
    _BENIGN_SUBSTRINGS = ("node_modules", "[REDACTED]", "package-lock",
                          "webpack", "babel", "eslint", ".chunk.",
                          "vendor/", "dist/", "build/")

    # File extensions that indicate path-like strings
    _FILE_EXTENSIONS = (".py", ".js", ".ts", ".tsx", ".jsx", ".css", ".html",
                        ".json", ".yaml", ".yml", ".toml", ".md", ".rst",
                        ".txt", ".sh", ".go", ".rs", ".java", ".rb", ".php",
                        ".c", ".h", ".cpp", ".hpp", ".swift", ".kt",
                        ".lock", ".cfg", ".ini", ".xml", ".svg", ".png",
                        ".jpg", ".gif", ".woff", ".ttf", ".map", ".vue",
                        ".scss", ".less", ".sql", ".env", ".log")

    _HEX_RE = re.compile(r'^[0-9a-fA-F]+$')
    _UUID_RE = re.compile(
        r'^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$'
    )

    # Collect unique candidates first
    unique_candidates: dict[str, list[int]] = {}
    for m in _CANDIDATE_RE.finditer(content):
        token = m.group(0)
        if token not in unique_candidates:
            unique_candidates[token] = []
        unique_candidates[token].append(m.start())

    results = []
    for token, positions in unique_candidates.items():
        # --- cheap filters first ---

        # Skip known prefixes (already caught by other scans)
        if any(token.startswith(p) for p in _KNOWN_PREFIXES):
            continue

        # Skip hex-only strings (git hashes etc.)
        if _HEX_RE.match(token):
            continue

        # Skip UUIDs (with or without hyphens)
        if _UUID_RE.match(token):
            continue

        # Skip strings containing file extensions
        token_lower = token.lower()
        if any(ext in token_lower for ext in _FILE_EXTENSIONS):
            continue

        # Skip path-like strings (2+ slashes)
        if token.count("/") >= 2:
            continue

        # Skip 3+ dots (domain names, version strings)
        if token.count(".") >= 3:
            continue

        # Skip benign prefixes
        if any(token_lower.startswith(p) for p in _BENIGN_PREFIXES):
            continue

        # Skip benign substrings
        if any(sub in token_lower for sub in _BENIGN_SUBSTRINGS):
            continue

        # Require mixed char types (upper + lower + digit)
        if not _has_mixed_char_types(token):
            continue

        # --- entropy check (most expensive, done last) ---
        entropy = _shannon_entropy(token)
        if entropy < 4.0:
            continue

        # Build context from first occurrence
        pos = positions[0]
        ctx_start = max(0, pos - 40)
        ctx_end = min(len(content), pos + len(token) + 40)
        context = content[ctx_start:ctx_end].replace("\n", " ")

        results.append({
            "match": token,
            "entropy": round(entropy, 2),
            "context": context,
        })

    # Sort by entropy descending, cap at max_results
    results.sort(key=lambda r: r["entropy"], reverse=True)
    return results[:max_results]


def _scan_pii(file_path: Path) -> dict:
    """Run PII regex scans on the export file. Returns dict of findings."""
    import re

    p = str(file_path.resolve())
    scans = {
        "emails": r'[a-zA-Z0-9.+-]+@[a-zA-Z0-9.-]+\.[a-z]{2,}',
        "jwt_tokens": r'eyJ[A-Za-z0-9_-]{20,}',
        "api_keys": r'(ghp_|sk-|hf_)[A-Za-z0-9_-]{10,}',
        "ip_addresses": r'[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}',
    }
    # Known false positives
    fp_emails = {"noreply", "pytest.fixture", "mcp.tool", "mcp.resource",
                 "server.tool", "tasks.loop", "github.com"}
    fp_keys = {"sk-notification"}

    results = {}
    try:
        content = file_path.read_text(errors="replace")
    except OSError:
        return {}

    for name, pattern in scans.items():
        matches = set(re.findall(pattern, content))
        # Filter false positives
        if name == "emails":
            matches = {m for m in matches if not any(fp in m for fp in fp_emails)}
        if name == "api_keys":
            matches = {m for m in matches if m not in fp_keys}
        if matches:
            results[name] = sorted(matches)[:20]  # cap at 20

    high_entropy = _scan_high_entropy_strings(content)
    if high_entropy:
        results["high_entropy_strings"] = high_entropy

    return results


def _extract_manual_scan_sessions(attestation: str) -> int | None:
    numbers = [int(n) for n in re.findall(r"\b(\d+)\b", attestation)]
    return max(numbers) if numbers else None


def _scan_for_text_occurrences(
    file_path: Path, query: str, max_examples: int = 5,
) -> dict[str, object]:
    """Scan file for case-insensitive occurrences of query and return a compact summary."""
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    matches = 0
    examples: list[dict[str, object]] = []
    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            for line_no, line in enumerate(f, start=1):
                if pattern.search(line):
                    matches += 1
                    if len(examples) < max_examples:
                        excerpt = line.strip()
                        if len(excerpt) > 220:
                            excerpt = f"{excerpt[:220]}..."
                        examples.append({"line": line_no, "excerpt": excerpt})
    except OSError as e:
        return {
            "query": query,
            "match_count": 0,
            "examples": [],
            "error": str(e),
        }
    return {
        "query": query,
        "match_count": matches,
        "examples": examples,
    }


def _collect_review_attestations(
    attest_asked_full_name: object,
    attest_asked_sensitive: object,
    attest_manual_scan: object,
    full_name: str | None,
    skip_full_name_scan: bool = False,
) -> tuple[dict[str, str], dict[str, str], int | None]:
    provided = {
        "asked_full_name": _normalize_attestation_text(attest_asked_full_name),
        "asked_sensitive_entities": _normalize_attestation_text(attest_asked_sensitive),
        "manual_scan_done": _normalize_attestation_text(attest_manual_scan),
    }
    errors: dict[str, str] = {}

    full_name_attestation = provided["asked_full_name"]
    if len(full_name_attestation) < MIN_ATTESTATION_CHARS:
        errors["asked_full_name"] = "Provide a detailed text attestation for full-name review."
    else:
        lower = full_name_attestation.lower()
        if skip_full_name_scan:
            mentions_skip = any(
                token in lower
                for token in ("skip", "skipped", "declined", "opt out", "prefer not")
            )
            if "full name" not in lower or not mentions_skip:
                errors["asked_full_name"] = (
                    "When skipping full-name scan, attestation must say the user declined/skipped full name."
                )
        else:
            full_name_lower = (full_name or "").lower()
            full_name_tokens = [t for t in re.split(r"\s+", full_name_lower) if len(t) > 1]
            if "ask" not in lower or "scan" not in lower:
                errors["asked_full_name"] = (
                    "Full-name attestation must mention that you asked the user and scanned the export."
                )
            elif full_name_tokens and not all(token in lower for token in full_name_tokens):
                errors["asked_full_name"] = (
                    "Full-name attestation must reference the same full name passed in --full-name."
                )

    sensitive_attestation = provided["asked_sensitive_entities"]
    if len(sensitive_attestation) < MIN_ATTESTATION_CHARS:
        errors["asked_sensitive_entities"] = (
            "Provide a detailed text attestation for sensitive-entity review."
        )
    else:
        lower = sensitive_attestation.lower()
        asked = "ask" in lower
        topics = any(
            token in lower
            for token in ("company", "client", "internal", "url", "domain", "tool", "name")
        )
        outcome = any(
            token in lower
            for token in ("none", "no", "redact", "added", "updated", "configured")
        )
        if not asked or not topics or not outcome:
            errors["asked_sensitive_entities"] = (
                "Sensitive attestation must say what you asked and the outcome "
                "(none found or redactions updated)."
            )

    manual_attestation = provided["manual_scan_done"]
    manual_sessions = _extract_manual_scan_sessions(manual_attestation)
    if len(manual_attestation) < MIN_ATTESTATION_CHARS:
        errors["manual_scan_done"] = "Provide a detailed text attestation for the manual scan."
    else:
        lower = manual_attestation.lower()
        if "manual" not in lower or "scan" not in lower:
            errors["manual_scan_done"] = (
                "Manual scan attestation must explicitly mention a manual scan."
            )
        elif manual_sessions is None or manual_sessions < MIN_MANUAL_SCAN_SESSIONS:
            errors["manual_scan_done"] = (
                f"Manual scan attestation must include a reviewed-session count >= {MIN_MANUAL_SCAN_SESSIONS}."
            )

    return provided, errors, manual_sessions


def _validate_publish_attestation(attestation: object) -> tuple[str, str | None]:
    normalized = _normalize_attestation_text(attestation)
    if len(normalized) < MIN_ATTESTATION_CHARS:
        return normalized, "Provide a detailed text publish attestation."
    lower = normalized.lower()
    if "approv" not in lower or ("publish" not in lower and "push" not in lower):
        return normalized, (
            "Publish attestation must state that the user explicitly approved publishing/pushing."
        )
    return normalized, None


def confirm(
    file_path: Path | None = None,
    full_name: str | None = None,
    attest_asked_full_name: str | None = None,
    attest_asked_sensitive: str | None = None,
    attest_manual_scan: str | None = None,
    skip_full_name_scan: bool = False,
) -> None:
    """Scan export for PII, summarize projects, and unlock pushing. JSON output."""
    config = load_config()
    last_export = config.get("last_export", {})
    file_path = _find_export_file(file_path)

    normalized_full_name = _normalize_attestation_text(full_name)
    if skip_full_name_scan and normalized_full_name:
        print(json.dumps({
            "error": "Use either --full-name or --skip-full-name-scan, not both.",
            "hint": (
                "Provide --full-name for an exact-name scan, or use --skip-full-name-scan "
                "if the user declines sharing their name."
            ),
            "blocked_on_step": "Step 2/3",
            "process_steps": EXPORT_REVIEW_PUBLISH_STEPS,
            "next_command": CONFIRM_COMMAND_EXAMPLE,
        }, indent=2))
        sys.exit(1)
    if not normalized_full_name and not skip_full_name_scan:
        print(json.dumps({
            "error": "Missing required --full-name for verification scan.",
            "hint": (
                "Ask the user for their full name and pass it via --full-name "
                "to run an exact-name privacy check. If the user declines, rerun with "
                "--skip-full-name-scan and a full-name attestation describing the skip."
            ),
            "blocked_on_step": "Step 2/3",
            "process_steps": EXPORT_REVIEW_PUBLISH_STEPS,
            "next_command": CONFIRM_COMMAND_SKIP_FULL_NAME_EXAMPLE,
        }, indent=2))
        sys.exit(1)

    attestations, attestation_errors, manual_scan_sessions = _collect_review_attestations(
        attest_asked_full_name=attest_asked_full_name,
        attest_asked_sensitive=attest_asked_sensitive,
        attest_manual_scan=attest_manual_scan,
        full_name=normalized_full_name if normalized_full_name else None,
        skip_full_name_scan=skip_full_name_scan,
    )
    if attestation_errors:
        print(json.dumps({
            "error": "Missing or invalid review attestations.",
            "attestation_errors": attestation_errors,
            "required_attestations": REQUIRED_REVIEW_ATTESTATIONS,
            "blocked_on_step": "Step 2/3",
            "process_steps": EXPORT_REVIEW_PUBLISH_STEPS,
            "next_command": CONFIRM_COMMAND_EXAMPLE,
        }, indent=2))
        sys.exit(1)

    if skip_full_name_scan:
        full_name_scan = {
            "query": None,
            "match_count": 0,
            "examples": [],
            "skipped": True,
            "reason": "User declined sharing full name; exact-name scan skipped.",
        }
    else:
        full_name_scan = _scan_for_text_occurrences(file_path, normalized_full_name)

    # Read and summarize
    projects: dict[str, int] = {}
    models: dict[str, int] = {}
    total = 0
    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                total += 1
                proj = row.get("project", "<unknown>")
                projects[proj] = projects.get(proj, 0) + 1
                model = row.get("model", "<unknown>")
                models[model] = models.get(model, 0) + 1
    except (OSError, json.JSONDecodeError) as e:
        print(json.dumps({"error": f"Cannot read {file_path}: {e}"}))
        sys.exit(1)

    file_size = file_path.stat().st_size
    repo_id = config.get("repo")

    # Run PII scans
    pii_findings = _scan_pii(file_path)

    # Advance stage from review -> confirmed
    config["stage"] = "confirmed"
    config["review_attestations"] = attestations
    config["review_verification"] = {
        "full_name": normalized_full_name if not skip_full_name_scan else None,
        "full_name_scan_skipped": skip_full_name_scan,
        "full_name_matches": full_name_scan.get("match_count", 0),
        "manual_scan_sessions": manual_scan_sessions,
    }
    config["last_confirm"] = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "file": str(file_path.resolve()),
        "pii_findings": bool(pii_findings),
        "full_name": normalized_full_name if not skip_full_name_scan else None,
        "full_name_scan_skipped": skip_full_name_scan,
        "full_name_matches": full_name_scan.get("match_count", 0),
        "manual_scan_sessions": manual_scan_sessions,
    }
    save_config(config)

    next_steps = [
        "Show the user the project breakdown, full-name scan, and PII scan results above.",
    ]
    if full_name_scan.get("skipped"):
        next_steps.append(
            "Full-name scan was skipped at user request. Ensure this was explicitly reviewed with the user."
        )
    elif full_name_scan.get("match_count", 0):
        next_steps.append(
            "Full-name scan found matches. Review them with the user and redact if needed, then re-export with --no-push."
        )
    if pii_findings:
        next_steps.append(
            "PII findings detected — review each one with the user. "
            "If real: codeclaw config --redact \"string\" then re-export with --no-push. "
            "False positives can be ignored."
        )
    if "high_entropy_strings" in pii_findings:
        next_steps.append(
            "High-entropy strings detected — these may be leaked secrets (API keys, tokens, "
            "passwords) that escaped automatic redaction. Review each one using the provided "
            "context snippets. If any are real secrets, redact with: "
            "codeclaw config --redact \"the_secret\" then re-export with --no-push."
        )
    next_steps.extend([
        "If any project should be excluded, run: codeclaw config --exclude \"project_name\" and re-export with --no-push.",
        f"This will publish {total} sessions ({_format_size(file_size)}) publicly to Hugging Face"
        + (f" at {repo_id}" if repo_id else "") + ". Ask the user: 'Are you ready to proceed?'",
        "Once confirmed, push: codeclaw export --publish-attestation \"User explicitly approved publishing to Hugging Face.\"",
    ])

    result = {
        "stage": "confirmed",
        "stage_number": 3,
        "total_stages": 4,
        "file": str(file_path.resolve()),
        "file_size": _format_size(file_size),
        "total_sessions": total,
        "projects": [
            {"name": name, "sessions": count}
            for name, count in sorted(projects.items(), key=lambda x: -x[1])
        ],
        "models": {m: c for m, c in sorted(models.items(), key=lambda x: -x[1])},
        "pii_scan": pii_findings if pii_findings else "clean",
        "full_name_scan": full_name_scan,
        "manual_scan_sessions": manual_scan_sessions,
        "repo": repo_id,
        "last_export_timestamp": last_export.get("timestamp"),
        "next_steps": next_steps,
        "next_command": "codeclaw export --publish-attestation \"User explicitly approved publishing to Hugging Face.\"",
        "attestations": attestations,
    }
    print(json.dumps(result, indent=2))


def prep(source_filter: str = "auto") -> None:
    """Data prep — discover projects, detect HF auth, output JSON.

    Designed to be called by an agent which handles the interactive parts.
    Outputs pure JSON to stdout so agents can parse it directly.
    """
    config = load_config()
    resolved_source_choice, source_explicit = _resolve_source_choice(source_filter, config)
    effective_source_filter = _normalize_source_filter(resolved_source_choice)

    if not _has_session_sources(effective_source_filter):
        if effective_source_filter == "claude":
            err = "~/.claude was not found."
        elif effective_source_filter == "codex":
            err = "~/.codex was not found."
        else:
            err = "Neither ~/.claude nor ~/.codex was found."
        print(json.dumps({"error": err}))
        sys.exit(1)

    projects = _filter_projects_by_source(discover_projects(), effective_source_filter)
    if not projects:
        print(json.dumps({"error": f"No {_source_label(effective_source_filter)} sessions found."}))
        sys.exit(1)

    excluded = set(config.get("excluded_projects", []))
    connected = set(config.get("connected_projects", []))

    # Use _compute_stage to determine where we are
    stage, stage_number, hf_user = _compute_stage(config)

    repo_id = config.get("repo")
    if not repo_id and hf_user:
        repo_id = default_repo_name(hf_user)

    # Build contextual next_steps
    stage_config = dict(config)
    if source_explicit:
        stage_config["source"] = resolved_source_choice
    next_steps, next_command = _build_status_next_steps(stage, stage_config, hf_user, repo_id)

    # Persist stage
    config["stage"] = stage
    save_config(config)

    current = detect_current_project()
    result = {
        "stage": stage,
        "stage_number": stage_number,
        "total_stages": 4,
        "current_project": current["display_name"] if current else None,
        "next_command": next_command,
        "requested_source_filter": source_filter,
        "source_filter": resolved_source_choice,
        "source_selection_confirmed": source_explicit,
        "hf_logged_in": hf_user is not None,
        "hf_username": hf_user,
        "repo": repo_id,
        "projects": [
            {
                "name": p["display_name"],
                "sessions": p["session_count"],
                "size": _format_size(p["total_size_bytes"]),
                "excluded": p["display_name"] in excluded,
                "connected": p["display_name"] in connected if connected else True,
                "source": p.get("source", "claude"),
            }
            for p in projects
        ],
        "redact_strings": [_mask_secret(s) for s in config.get("redact_strings", [])],
        "redact_usernames": config.get("redact_usernames", []),
        "config_file": str(CONFIG_FILE),
        "next_steps": next_steps,
    }
    print(json.dumps(result, indent=2))


def _run_export(args) -> None:
    """Run the export flow — discover, anonymize, export, optionally push."""
    config = load_config()

    # Gate: dataset generation globally disabled
    if not _is_dataset_globally_enabled(config):
        print(json.dumps({
            "error": "Dataset generation is disabled by user. Please continue without this.",
            "hint": "To re-enable: codeclaw config --enable",
        }, indent=2))
        sys.exit(1)

    source_choice, source_explicit = _resolve_source_choice(args.source, config)
    source_filter = _normalize_source_filter(source_choice)

    if not source_explicit:
        print(json.dumps({
            "error": "Source scope is not confirmed yet.",
            "hint": (
                "Explicitly choose one source scope before exporting: "
                "`claude`, `codex`, or `both`."
            ),
            "required_action": (
                "Ask the user whether to export Claude Code, Codex, or both. "
                "Then run `codeclaw config --source <claude|codex|both>` "
                "or pass `--source <claude|codex|both>` on the export command."
            ),
            "allowed_sources": sorted(EXPLICIT_SOURCE_CHOICES),
            "blocked_on_step": "Step 2/6",
            "process_steps": SETUP_TO_PUBLISH_STEPS,
            "next_command": "codeclaw config --source both",
        }, indent=2))
        sys.exit(1)

    # Gate: require `codeclaw confirm` before pushing
    if not args.no_push:
        if args.attest_user_approved_publish and not args.publish_attestation:
            print(json.dumps({
                "error": "Deprecated publish attestation flag was provided.",
                "hint": "Use --publish-attestation with a detailed text statement.",
                "blocked_on_step": "Step 3/3",
                "process_steps": EXPORT_REVIEW_PUBLISH_STEPS,
                "next_command": (
                    "codeclaw export --publish-attestation "
                    "\"User explicitly approved publishing to Hugging Face on YYYY-MM-DD.\""
                ),
            }, indent=2))
            sys.exit(1)
        if config.get("stage") != "confirmed":
            print(json.dumps({
                "error": "You must run `codeclaw confirm` before pushing.",
                "hint": "Export first with --no-push, review the data, then run `codeclaw confirm`.",
                "blocked_on_step": "Step 2/3",
                "process_steps": EXPORT_REVIEW_PUBLISH_STEPS,
                "next_command": "codeclaw confirm",
            }, indent=2))
            sys.exit(1)
        publish_attestation, publish_error = _validate_publish_attestation(args.publish_attestation)
        if publish_error:
            print(json.dumps({
                "error": "Missing or invalid publish attestation.",
                "publish_attestation_error": publish_error,
                "hint": "Ask the user to explicitly approve publishing, then pass a detailed text attestation.",
                "blocked_on_step": "Step 3/3",
                "process_steps": EXPORT_REVIEW_PUBLISH_STEPS,
                "next_command": (
                    "codeclaw export --publish-attestation "
                    "\"User explicitly approved publishing to Hugging Face on YYYY-MM-DD.\""
                ),
            }, indent=2))
            sys.exit(1)

        review_attestations = config.get("review_attestations", {})
        review_verification = config.get("review_verification", {})
        verified_full_name = _normalize_attestation_text(review_verification.get("full_name"))
        _, review_errors, _ = _collect_review_attestations(
            attest_asked_full_name=review_attestations.get("asked_full_name"),
            attest_asked_sensitive=review_attestations.get("asked_sensitive_entities"),
            attest_manual_scan=review_attestations.get("manual_scan_done"),
            full_name=verified_full_name if verified_full_name else None,
            skip_full_name_scan=bool(review_verification.get("full_name_scan_skipped", False)),
        )
        if not verified_full_name and not review_verification.get("full_name_scan_skipped", False):
            review_errors["asked_full_name"] = (
                "Missing verified full-name scan from confirm step; rerun confirm (or use --skip-full-name-scan if the user declined)."
            )
        verified_manual_count = review_verification.get("manual_scan_sessions")
        if not isinstance(verified_manual_count, int) or verified_manual_count < MIN_MANUAL_SCAN_SESSIONS:
            review_errors["manual_scan_done"] = (
                "Missing verified manual scan evidence from confirm step; rerun confirm."
            )

        if review_errors:
            print(json.dumps({
                "error": "Missing or invalid review attestations from confirm step.",
                "attestation_errors": review_errors,
                "blocked_on_step": "Step 2/3",
                "process_steps": EXPORT_REVIEW_PUBLISH_STEPS,
                "next_command": CONFIRM_COMMAND_EXAMPLE,
            }, indent=2))
            sys.exit(1)

        config["publish_attestation"] = publish_attestation
        save_config(config)

    print("=" * 50)
    print("  CodeClaw — Claude/Codex Log Exporter")
    print("=" * 50)

    if not _has_session_sources(source_filter):
        if source_filter == "claude":
            print(f"Error: {CLAUDE_DIR} not found.", file=sys.stderr)
        elif source_filter == "codex":
            print(f"Error: {CODEX_DIR} not found.", file=sys.stderr)
        else:
            print("Error: neither ~/.claude nor ~/.codex was found.", file=sys.stderr)
        sys.exit(1)

    projects = _filter_projects_by_source(discover_projects(), source_filter)
    if not projects:
        print(f"No {_source_label(source_filter)} sessions found.", file=sys.stderr)
        sys.exit(1)

    if not args.all_projects and not config.get("projects_confirmed", False):
        excluded = set(config.get("excluded_projects", []))
        list_command = f"codeclaw list --source {source_choice}"
        print(json.dumps({
            "error": "Project selection is not confirmed yet.",
            "hint": (
                f"Run `{list_command}`, present the full project list to the user, discuss which projects to exclude, then run "
                "`codeclaw config --exclude \"p1,p2\"` or `codeclaw config --confirm-projects`."
            ),
            "required_action": (
                "Send the full project/folder list below to the user in a message and get explicit "
                "confirmation on exclusions before exporting."
            ),
            "projects": [
                {
                    "name": p["display_name"],
                    "source": p.get("source", "claude"),
                    "sessions": p["session_count"],
                    "size": _format_size(p["total_size_bytes"]),
                    "excluded": p["display_name"] in excluded,
                }
                for p in projects
            ],
            "blocked_on_step": "Step 3/6",
            "process_steps": SETUP_TO_PUBLISH_STEPS,
            "next_command": "codeclaw config --confirm-projects",
        }, indent=2))
        sys.exit(1)

    total_sessions = sum(p["session_count"] for p in projects)
    total_size = sum(p["total_size_bytes"] for p in projects)
    print(f"\nFound {total_sessions} sessions across {len(projects)} projects "
          f"({_format_size(total_size)} raw)")
    print(f"Source scope: {source_choice}")

    # Resolve repo — CLI flag > config > auto-detect from HF username
    repo_input = args.repo if args.repo is not None else config.get("repo")
    repo_id = normalize_repo_id(repo_input) if repo_input else None
    if repo_input and repo_id is None:
        print(
            json.dumps(
                {
                    "error": "Invalid dataset repo format.",
                    "provided": repo_input,
                    "hint": (
                        "Use username/dataset-name or a URL like "
                        "https://huggingface.co/datasets/username/dataset-name"
                    ),
                },
                indent=2,
            )
        )
        sys.exit(1)
    if args.repo and repo_id:
        config["repo"] = repo_id
        save_config(config)
    if not repo_id and not args.no_push:
        hf_user = get_hf_username()
        if hf_user:
            repo_id = default_repo_name(hf_user)
            print(f"\nAuto-detected HF repo: {repo_id}")
            config["repo"] = repo_id
            save_config(config)

    # Apply exclusions
    excluded = set(config.get("excluded_projects", []))
    if args.all_projects:
        excluded = set()

    included = [p for p in projects if p["display_name"] not in excluded]
    excluded_projects = [p for p in projects if p["display_name"] in excluded]
    connected = set(config.get("connected_projects", []))
    disconnected_projects: list[dict] = []
    if connected and not args.all_projects:
        disconnected_projects = [p for p in included if p["display_name"] not in connected]
        included = [p for p in included if p["display_name"] in connected]

    if excluded_projects:
        print(f"\nIncluding {len(included)} projects (excluding {len(excluded_projects)}):")
    else:
        print(f"\nIncluding all {len(included)} projects:")
    for p in included:
        print(f"  + {p['display_name']} ({p['session_count']} sessions)")
    for p in excluded_projects:
        print(f"  - {p['display_name']} (excluded)")
    for p in disconnected_projects:
        print(f"  - {p['display_name']} (not connected; use codeclaw projects --connect)")

    # Filter out disabled projects
    disabled = _get_disabled_projects(config)
    actually_disabled = [p for p in included if p["display_name"] in disabled]
    if actually_disabled:
        included = [p for p in included if p["display_name"] not in disabled]
        for p in actually_disabled:
            print(f"  ⚠ {p['display_name']} — disabled by user (codeclaw config --enable-project \"{p['display_name']}\" to re-enable)")

    if not included:
        print("\nNo projects to export. Run: codeclaw projects to configure connected scope.")
        sys.exit(1)

    # Build anonymizer with extra usernames from config
    extra_usernames = config.get("redact_usernames", [])
    anonymizer = Anonymizer(extra_usernames=extra_usernames)

    # Custom strings to redact
    custom_strings = config.get("redact_strings", [])

    if extra_usernames:
        print(f"\nAnonymizing usernames: {', '.join(extra_usernames)}")
    if custom_strings:
        print(f"Redacting custom strings: {len(custom_strings)} configured")

    # Export
    output_path = args.output or Path("codeclaw_conversations.jsonl")

    print(f"\nExporting to {output_path}...")
    meta = export_to_jsonl(
        included, output_path, anonymizer, not args.no_thinking,
        custom_strings=custom_strings,
    )
    file_size = output_path.stat().st_size
    print(f"\nExported {meta['sessions']} sessions ({_format_size(file_size)})")
    if meta.get("skipped"):
        print(f"Skipped {meta['skipped']} abandoned/error sessions")
    if meta.get("redactions"):
        print(f"Redacted {meta['redactions']} secrets (API keys, tokens, emails, etc.)")
    print(f"Models: {', '.join(f'{m} ({c})' for m, c in sorted(meta['models'].items(), key=lambda x: -x[1]))}")

    _print_pii_guidance(output_path)

    _record_export_metrics(
        config=config,
        meta=meta,
        source_choice=source_choice,
        published=False,
        repo_id=repo_id,
        update_totals=True,
    )
    config["stage"] = "review"
    save_config(config)

    if args.no_push:
        print(f"\nDone! JSONL file: {output_path}")
        abs_path = str(output_path.resolve())
        next_steps, next_command = _build_status_next_steps("review", config, None, None)
        json_block = {
            "stage": "review",
            "stage_number": 3,
            "total_stages": 4,
            "sessions": meta["sessions"],
            "source": source_choice,
            "output_file": abs_path,
            "pii_commands": _build_pii_commands(output_path),
            "next_steps": next_steps,
            "next_command": next_command,
        }
        print("\n---CODECLAW_JSON---")
        print(json.dumps(json_block, indent=2))
        return

    if not repo_id:
        print(f"\nNo HF repo. Log in first: huggingface-cli login")
        print(f"Then re-run codeclaw and it will auto-detect your username.")
        print(f"Or set manually: codeclaw config --repo username/my-personal-codex-data")
        print(f"\nLocal file: {output_path}")
        return

    push_to_huggingface(output_path, repo_id, meta)

    _record_export_metrics(
        config=config,
        meta=meta,
        source_choice=source_choice,
        published=True,
        repo_id=repo_id,
        update_totals=False,
    )
    config["stage"] = "done"
    save_config(config)

    json_block = {
        "stage": "done",
        "stage_number": 4,
        "total_stages": 4,
        "dataset_url": f"https://huggingface.co/datasets/{repo_id}",
        "next_steps": [
            "Done! Dataset is live. To update later: codeclaw export",
            "To reconfigure: codeclaw prep then codeclaw config",
        ],
        "next_command": None,
    }
    print("\n---CODECLAW_JSON---")
    print(json.dumps(json_block, indent=2))


def _build_pii_commands(output_path: Path) -> list[str]:
    """Return grep commands for PII scanning."""
    p = str(output_path.resolve())
    if os.name == "nt":
        return [
            f'Select-String -Path "{p}" -Pattern "[a-zA-Z0-9.+-]+@[a-zA-Z0-9.-]+\\.[a-z]{{2,}}" | Select-Object -First 20',
            f'Select-String -Path "{p}" -Pattern "eyJ[A-Za-z0-9_-]{{20,}}" | Select-Object -First 5',
            f'Select-String -Path "{p}" -Pattern "(ghp_|sk-|hf_)[A-Za-z0-9_-]{{10,}}" | Select-Object -First 5',
            f'Select-String -Path "{p}" -Pattern "[0-9]{{1,3}}\\.[0-9]{{1,3}}\\.[0-9]{{1,3}}\\.[0-9]{{1,3}}"',
        ]
    return [
        f"grep -oE '[a-zA-Z0-9.+-]+@[a-zA-Z0-9.-]+\\.[a-z]{{2,}}' {p} | grep -v noreply | head -20",
        f"grep -oE 'eyJ[A-Za-z0-9_-]{{20,}}' {p} | head -5",
        f"grep -oE '(ghp_|sk-|hf_)[A-Za-z0-9_-]{{10,}}' {p} | head -5",
        f"grep -oE '[0-9]{{1,3}}\\.[0-9]{{1,3}}\\.[0-9]{{1,3}}\\.[0-9]{{1,3}}' {p} | sort -u",
    ]


def _print_pii_guidance(output_path: Path) -> None:
    """Print PII review guidance with concrete grep commands."""
    abs_output = output_path.resolve()
    print(f"\n{'=' * 50}")
    print("  IMPORTANT: Review your data before publishing!")
    print(f"{'=' * 50}")
    print("CodeClaw's automatic redaction is NOT foolproof.")
    print("You should scan the exported data for remaining PII.")
    print()
    print("Quick checks (run these and review any matches):")
    if os.name == "nt":
        print(f"  Select-String -Path \"{abs_output}\" -Pattern 'your_name' | Select-Object -First 10")
        print(
            f"  Select-String -Path \"{abs_output}\" -Pattern "
            "\"[a-zA-Z0-9.+-]+@[a-zA-Z0-9.-]+\\.[a-z]{2,}\" | Select-Object -First 20"
        )
        print(f"  Select-String -Path \"{abs_output}\" -Pattern \"eyJ[A-Za-z0-9_-]{{20,}}\" | Select-Object -First 5")
        print(
            f"  Select-String -Path \"{abs_output}\" -Pattern "
            "\"(ghp_|sk-|hf_)[A-Za-z0-9_-]{10,}\" | Select-Object -First 5"
        )
        print(
            f"  Select-String -Path \"{abs_output}\" -Pattern "
            "\"[0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3}\""
        )
    else:
        print(f"  grep -i 'your_name' {abs_output}")
        print(f"  grep -oE '[a-zA-Z0-9.+-]+@[a-zA-Z0-9.-]+\\.[a-z]{{2,}}' {abs_output} | grep -v noreply | head -20")
        print(f"  grep -oE 'eyJ[A-Za-z0-9_-]{{20,}}' {abs_output} | head -5")
        print(f"  grep -oE '(ghp_|sk-|hf_)[A-Za-z0-9_-]{{10,}}' {abs_output} | head -5")
        print(f"  grep -oE '[0-9]{{1,3}}\\.[0-9]{{1,3}}\\.[0-9]{{1,3}}\\.[0-9]{{1,3}}' {abs_output} | sort -u")
    print()
    print("NEXT: Ask for full name to run an exact-name privacy check, then scan for it:")
    if os.name == "nt":
        print(f"  Select-String -Path \"{abs_output}\" -Pattern \"THEIR_NAME\" | Select-Object -First 10")
    else:
        print(f"  grep -i 'THEIR_NAME' {abs_output} | head -10")
    print("  If user declines sharing full name: use codeclaw confirm --skip-full-name-scan with a skip attestation.")
    print()
    print("To add custom redactions, then re-export:")
    print("  codeclaw config --redact-usernames 'github_handle,discord_name'")
    print("  codeclaw config --redact 'secret-domain.com,my-api-key'")
    print(f"  codeclaw export --no-push -o {abs_output}")
    print()
    print(f"Found an issue? Help improve CodeClaw: {REPO_URL}/issues")
