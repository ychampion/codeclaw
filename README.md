# CodeClaw

CodeClaw converts Claude Code and Codex sessions into privacy-safe training datasets with review gates, background sync, and MCP memory retrieval.

[![Tests](https://github.com/ychampion/codeclaw/actions/workflows/test.yml/badge.svg)](https://github.com/ychampion/codeclaw/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/codeclaw)](https://pypi.org/project/codeclaw/)
[![Release](https://img.shields.io/github/v/release/ychampion/codeclaw)](https://github.com/ychampion/codeclaw/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/pypi/pyversions/codeclaw)](https://pypi.org/project/codeclaw/)

## TL;DR

- Run `codeclaw setup` once.
- Run `codeclaw export --no-push` to produce a local reviewed dataset.
- Run `codeclaw confirm ...` to pass review gates.
- Run `codeclaw export --publish-attestation "..."` only after explicit approval.

## Default UX

- Running plain `codeclaw` opens the full-screen TUI.
- Running `codeclaw export ...` keeps the scripted CLI flow.

## Why CodeClaw

- Turn day-to-day coding sessions into structured, reusable training data.
- Keep privacy controls first-class with redaction and manual review gates.
- Preserve historical problem-solving context through MCP-accessible session memory.

## Core Capabilities

- Multi-source ingestion:
  - Claude Code and Codex session discovery and parsing.
  - Experimental adapter routing for Cursor, Windsurf, Aider, Continue.dev, Antigravity, VS Code, Zed, and Xcode beta logs.
- Privacy-aware export:
  - Secret and PII redaction, username anonymization, and project-level exclusions.
  - Layered privacy engine: regex baseline + optional ML NER (`codeclaw[pii-ml]`).
- Controlled publishing workflow:
  - Local export, user review attestations, confirm gate, then push.
  - Immutable dataset version snapshots + dedupe index on publish.
- Continuous mode:
  - Background watch daemon for incremental sync.
- Memory tooling:
  - MCP server with search, project patterns, trajectory stats, session lookup, graph similarity retrieval, and index refresh.

## Install

```bash
pip install codeclaw
```

Optional extras:

```bash
pip install "codeclaw[pii-ml]"    # Presidio + spaCy detection layer
pip install "codeclaw[mcp]"       # MCP server runtime
pip install "codeclaw[finetune]"  # Experimental local fine-tune scaffolding
```

From source:

```bash
git clone https://github.com/ychampion/codeclaw.git
cd codeclaw
pip install -e ".[dev]"
```

## Quick Start

```bash
# Guided onboarding (HF auth help, repo setup, project scope, MCP, watcher)
codeclaw setup

# Verify environment and connected scope
codeclaw doctor
codeclaw projects --source both
codeclaw stats
codeclaw diff --format json
codeclaw config --encryption status

# Export locally first
codeclaw export --no-push

# Review and confirm
codeclaw confirm \
  --full-name "YOUR FULL NAME" \
  --attest-full-name "Asked for full name and scanned export." \
  --attest-sensitive "Reviewed for company/client/private identifiers." \
  --attest-manual-scan "Manually reviewed representative sessions."

# Publish only after explicit approval
codeclaw export --publish-attestation "User explicitly approved publishing to Hugging Face."

# Optional one-command sharing flow
codeclaw share --publish --publish-attestation "User explicitly approved publishing to Hugging Face."
```

## Commands

| Command | Description |
|---------|-------------|
| `codeclaw status` | Show current stage and next steps (JSON) |
| `codeclaw prep` | Discover projects and auth state |
| `codeclaw setup` | Guided onboarding (HF, dataset repo, projects, MCP, watcher) |
| `codeclaw doctor` | Verify logs, HF auth, and MCP registration |
| `codeclaw stats` | Show session, token, redaction, and export metrics |
| `codeclaw stats --skill` | Include trajectory-based growth metrics |
| `codeclaw diff` | Preview exactly what would be redacted before confirm |
| `codeclaw projects` | Manage connected project scope |
| `codeclaw list` | List projects with source, size, and exclusion state |
| `codeclaw config ...` | Configure repo, sources, exclusions, and redactions |
| `codeclaw config --encryption on|off|status` | Manage encryption-at-rest mode |
| `codeclaw export --no-push` | Export locally for review |
| `codeclaw export --dry-run` | Preview what would be exported/published without writing files |
| `codeclaw confirm ...` | Run checks and unlock push gate |
| `codeclaw export --publish-attestation "..."` | Push dataset after approval |
| `codeclaw share [--publish]` | Fast export flow with optional publish + dataset card update |
| `codeclaw watch --start|--stop|--status|--now|--pause|--resume` | Manage background sync daemon lifecycle |
| `codeclaw watch --logs [--follow]` | View daemon logs with optional streaming |
| `codeclaw watch --monitor [--follow]` | Live watch monitor (status + recent activity) |
| `codeclaw watch --switch-project "<name>"` | Quickly scope watcher to one project |
| `codeclaw watch --set-projects "a,b"` | Set connected project scope directly |
| `codeclaw console` | Interactive slash-command terminal (`/status`, `/logs`, `/scope`, `/run`) |
| `codeclaw tui` | Full-screen TUI with activity feed, slash commands, jobs, and plugins |
| `codeclaw serve` | Start MCP server over stdio |
| `codeclaw install-mcp` | Register MCP server in Claude config |
| `codeclaw finetune --experimental` | Preview fine-tune scaffold for local experimentation |
| `codeclaw synthesize --project <name>` | Generate `CODECLAW.md` from synced sessions |
| `codeclaw update-skill claude` | Install/update local CodeClaw skill |

Additional source filters are available for adapter-backed ingestion:

- `cursor`, `windsurf`, `aider`, `continue`, `antigravity`, `vscode`, `zed`, `xcode-beta`

Watch transparency examples:

```bash
codeclaw watch --status
codeclaw watch --monitor --follow
codeclaw watch --logs --follow
codeclaw watch --pause
codeclaw watch --resume
codeclaw watch --switch-project "codex:codeclaw"
```

Interactive console mode:

```bash
codeclaw console --source codex
# Then inside the prompt:
/status
/projects
/scope codex:codeclaw
/logs 80
/run export --no-push
```

Full-screen TUI mode:

```bash
codeclaw
# equivalent explicit command:
codeclaw tui --source both
```

Inside the TUI:

```text
/help
/status
/watch on
/export --dry-run
/jobs
/plugins list
```

Minimal local plugin example (`./plugins/echo`):

```text
plugins/
  echo/
    plugin.json
    plugin.py
```

`plugin.json`:

```json
{
  "name": "echo",
  "version": "0.1.0",
  "entrypoint": "plugin.py",
  "description": "Simple echo command"
}
```

`plugin.py`:

```python
from codeclaw.tui.types import CommandResult


def register(ctx):
    def _echo(_app, args):
        return CommandResult(ok=True, message=" ".join(args) if args else "echo")

    ctx.register_command("echo", _echo, "Echo input text", usage="/echo <text>")
```

## MCP Memory Server

Install optional MCP dependency:

```bash
pip install "codeclaw[mcp]"
codeclaw install-mcp
```

Available MCP tools:

- `search_past_solutions(query, max_results=5)`
- `get_project_patterns(project=None)`
- `get_trajectory_stats()`
- `get_session(session_id)`
- `find_similar_sessions(context, max_results=5)`
- `refresh_index()`

## Privacy and Safety

CodeClaw is designed for private-by-default workflows:

- path and username anonymization
- secret and high-entropy token detection
- custom redaction lists
- manual confirmation and attestation gates before publish
- encryption-at-rest support for local artifacts with keyring-backed key management

Automated redaction is not perfect. Always review local exports before publishing.

## Package Distribution

- Primary: PyPI (`pip install codeclaw`)
- Additional: GitHub Packages publish workflow is included for org/internal registry consumption.

Install from GitHub Packages:

```bash
pip install codeclaw \
  --index-url https://pypi.pkg.github.com/ychampion/simple/ \
  --extra-index-url https://pypi.org/simple
```

## README Sync Policy

README command docs are enforced in CI:

- `tests/test_docs_consistency.py` validates command naming and branding markers.
- A CLI help parity test ensures README command rows stay aligned with the real CLI surface.

If commands change, CI fails until README is updated.

## Community

- Contribution guide: [CONTRIBUTING.md](CONTRIBUTING.md)
- Security policy: [SECURITY.md](SECURITY.md)
- Support channels: [SUPPORT.md](SUPPORT.md)
- Code of conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- Release process: [RELEASE.md](RELEASE.md)

## License

MIT - see [LICENSE](LICENSE).
