# CodeClaw

CodeClaw exports Claude Code and Codex sessions into privacy-safe training datasets, with gated publish controls, automated sync workflows, and optional MCP memory tooling.

[![Tests](https://github.com/ychampion/codeclaw/actions/workflows/test.yml/badge.svg)](https://github.com/ychampion/codeclaw/actions/workflows/test.yml)
[![Release](https://img.shields.io/github/v/release/ychampion/codeclaw)](https://github.com/ychampion/codeclaw/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## Why CodeClaw

- Turn day-to-day coding sessions into structured, reusable training data.
- Keep privacy controls first-class with redaction and manual review gates.
- Preserve historical problem-solving context through MCP-accessible session memory.

## Core Capabilities

- Multi-source ingestion:
  - Claude Code and Codex session discovery and parsing.
- Privacy-aware export:
  - Secret and PII redaction, username anonymization, and project-level exclusions.
- Controlled publishing workflow:
  - Local export, user review attestations, confirm gate, then push.
- Continuous mode:
  - Background watch daemon for incremental sync.
- Memory tooling:
  - MCP server with search, project patterns, trajectory stats, session lookup, graph similarity retrieval, and index refresh.

## Install

```bash
pip install codeclaw
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
| `codeclaw projects` | Manage connected project scope |
| `codeclaw list` | List projects with source, size, and exclusion state |
| `codeclaw config ...` | Configure repo, sources, exclusions, and redactions |
| `codeclaw export --no-push` | Export locally for review |
| `codeclaw confirm ...` | Run checks and unlock push gate |
| `codeclaw export --publish-attestation "..."` | Push dataset after approval |
| `codeclaw share [--publish]` | Fast export flow with optional publish + dataset card update |
| `codeclaw watch --start|--stop|--status|--now` | Manage background sync daemon |
| `codeclaw serve` | Start MCP server over stdio |
| `codeclaw install-mcp` | Register MCP server in Claude config |
| `codeclaw synthesize --project <name>` | Generate `CODECLAW.md` from synced sessions |
| `codeclaw update-skill claude` | Install/update local CodeClaw skill |

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

Automated redaction is not perfect. Always review local exports before publishing.

## Community

- Contribution guide: [CONTRIBUTING.md](CONTRIBUTING.md)
- Security policy: [SECURITY.md](SECURITY.md)
- Support channels: [SUPPORT.md](SUPPORT.md)
- Code of conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- Release process: [RELEASE.md](RELEASE.md)

## License

MIT - see [LICENSE](LICENSE).
