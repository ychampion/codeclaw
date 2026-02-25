# CodeClaw

> **Turn your AI coding sessions into training data — automatically, in the background, without interrupting your workflow.**

CodeClaw watches your Claude Code and Codex session logs, redacts secrets and PII, classifies each conversation by trajectory type, and pushes structured training data to your private Hugging Face dataset. One install. Zero friction.

<img width="3168" height="1344" alt="codeclaw" src="https://github.com/user-attachments/assets/1fc5ab0e-a6b7-4b66-85f3-535e28da3740" />

Every session you have with an AI coding agent is a signal — a prompt, a solution, an error, a fix, a decision. CodeClaw captures that signal before it disappears. Over time, it compounds into a dataset that reflects your exact coding style, your stack, your patterns, and your instincts. That dataset is yours: to fine-tune future models, to feed into an MCP memory server, or to share with the world.

---

## Install

```bash
pip install codeclaw
```

Or from source:

```bash
git clone https://github.com/ychampion/codeclaw.git && pip install -e codeclaw/
```

---

## Give this to your agent

Paste this into Claude Code, Codex, or any coding agent to get started immediately:

```
Help me set up CodeClaw to export my Claude Code and Codex conversation history to Hugging Face.
Install it, install the skill, and walk me through the full flow.

STEP 1 — INSTALL
  pip install codeclaw
  If that fails: git clone https://github.com/ychampion/codeclaw.git /tmp/codeclaw && pip install /tmp/codeclaw

STEP 2 — INSTALL SKILL
  codeclaw update-skill claude

STEP 3 — START
  codeclaw prep
  Every codeclaw command outputs next_steps in its JSON — follow them through the entire flow.

STEP 3A — CHOOSE SOURCE SCOPE (REQUIRED BEFORE EXPORT)
  Ask the user explicitly: Claude Code, Codex, or both?
  codeclaw config --source "claude|codex|both"
  Do not export until source scope is explicitly confirmed.

STEP 3B — PRESENT ALL FOLDERS (REQUIRED BEFORE EXPORT)
  codeclaw list --source "claude|codex|both"
  Send the FULL project/folder list to the user (name, source, sessions, size, excluded).
  Ask which projects to exclude.
  codeclaw config --exclude "project1,project2" OR codeclaw config --confirm-projects
  Do not export until folder selection is explicitly confirmed.

STEP 4 — REVIEW + CONFIRM (REQUIRED BEFORE PUSH)
  codeclaw export --no-push --output /tmp/codeclaw_export.jsonl
  Review PII findings and apply excludes/redactions as needed.
  codeclaw confirm --full-name "THEIR FULL NAME" --attest-full-name "..." --attest-sensitive "..." --attest-manual-scan "..."
  If user declines sharing full name: use --skip-full-name-scan and attest the skip reason.

STEP 5 — PUBLISH (ONLY AFTER EXPLICIT USER APPROVAL)
  codeclaw export --publish-attestation "User explicitly approved publishing to Hugging Face."
  Never publish unless the user explicitly says yes.

IF ANY COMMAND FAILS DUE TO A SKIPPED STEP:
  Restate the 6-step checklist and resume from the blocked step.

IMPORTANT: Never run bare `huggingface-cli login` — always use --token.
IMPORTANT: Always export with --no-push first and review for PII before publishing.
```

---

## Background Daemon

CodeClaw can run silently in the background, capturing sessions automatically without any manual intervention:

```bash
codeclaw watch --start    # Start the background daemon
codeclaw watch --stop     # Stop it
codeclaw watch --status   # Check if it's running
codeclaw watch --now      # Trigger an immediate sync right now
```

The daemon:
- Watches `~/.claude/projects/` for new or modified session files using filesystem events (zero CPU when idle)
- Runs the full pipeline automatically: collect → redact → classify → format → push
- Buffers sessions in `~/.codeclaw/pending.jsonl` and auto-pushes when `min_sessions_before_push` is reached (default: 5)
- Logs all activity to `~/.codeclaw/daemon.log` with rotation at 10MB
- Survives reboots via launchd (macOS) or systemd user unit (Linux), installed automatically on setup
- Retries failed HF pushes with exponential backoff (30s → 120s → 300s)

Auto-push in daemon mode bypasses the manual attestation gate — this is intentional for private personal use. The gated flow remains available for public dataset publishing.

---

## How It Works

```
Claude Code session ends
        ↓
Daemon detects new JSONL in ~/.claude/projects/
        ↓
Parser reconstructs full conversation (messages, tool calls, thinking traces)
        ↓
Redactor strips secrets, paths, emails, high-entropy tokens
        ↓
Classifier assigns trajectory_type
        ↓
Formatter outputs SFT-ready JSONL with metadata
        ↓
Publisher pushes to data/<project>/train-<timestamp>.jsonl on HuggingFace
        ↓
CODECLAW.md knowledge base updated with dataset health stats
```

---

## Trajectory Classification

Every session is automatically tagged with a `trajectory_type` — this is the metadata that makes the dataset genuinely useful for fine-tuning, not just a log dump.

| Type | Pattern | Training Value |
|------|---------|----------------|
| `correction_loop` | Claude is wrong → you correct it → it fixes → success | ⬛⬛⬛⬛⬛ Highest |
| `debugging_trace` | Bash commands run + errors seen + root cause found | ⬛⬛⬛⬛⬛ Highest |
| `iterative_build` | Long multi-turn session building a feature incrementally | ⬛⬛⬛⬛ High |
| `refactor` | Existing code improved with reasoning explained | ⬛⬛⬛ Medium |
| `sft_clean` | Clean first-try solution, correct on first attempt | ⬛⬛ Medium |

`correction_loop` and `debugging_trace` sessions are the most valuable — they teach a model *what not to do* and *how to recover*, which plain SFT data almost never captures.

---

## Dataset Structure

CodeClaw uses a **per-project, multi-subset** HuggingFace dataset layout:

```
yourusername/dataset
├── data/
│   ├── project/          ← one folder per project
│   │   ├── train-20260225-143012-abc123.jsonl
│   │   └── train-20260226-091522-def456.jsonl
│   ├── my-api/
│   │   └── train-20260225-180034-ghi789.jsonl
│   └── personal/
│       └── train-20260224-220011-jkl012.jsonl
└── README.md             ← auto-generated dataset card with YAML configs
```

Each project is a named HF subset, loadable independently:

```python
from datasets import load_dataset

# Load one project
ds = load_dataset("yourusername/dataset", "project", split="train")

# Load everything
ds = load_dataset("yourusername/dataset", split="train")

# Filter by trajectory type
corrections = ds.filter(lambda x: x["metadata"]["trajectory_type"] == "correction_loop")
```

---

<details>
<summary><b>Manual usage (without an agent)</b></summary>

### Quick start

```bash
pip install codeclaw
huggingface-cli login --token YOUR_HF_TOKEN

# Discover your projects
codeclaw prep
codeclaw config --source both        # REQUIRED: claude, codex, or both
codeclaw list --source both           # Review all projects before export

# Configure
codeclaw config --repo username/cc-logs
codeclaw config --exclude "scratch,personal-notes"
codeclaw config --redact-usernames "my_github_handle"
codeclaw config --redact "my-company.com,internal-project-name"

# Export locally first (always)
codeclaw export --no-push

# Review and confirm (required before push)
codeclaw confirm \
  --full-name "YOUR FULL NAME" \
  --attest-full-name "Scanned export for YOUR FULL NAME — none found." \
  --attest-sensitive "Asked about company/client names and private URLs; redactions applied." \
  --attest-manual-scan "Manually reviewed 20 sessions from beginning/middle/end."

# Push
codeclaw export --publish-attestation "User explicitly approved publishing to Hugging Face."
```

### All commands

| Command | Description |
|---------|-------------|
| `codeclaw status` | Show current stage and next steps (JSON) |
| `codeclaw prep` | Discover projects, check HF auth |
| `codeclaw prep --source both` | Include both Claude Code + Codex |
| `codeclaw list` | List all projects with session counts and exclusion status |
| `codeclaw list --source codex` | List only Codex projects |
| `codeclaw config` | Show current config |
| `codeclaw config --repo user/cc-logs` | Set HuggingFace dataset repo |
| `codeclaw config --source claude\|codex\|both` | Set source scope (required before export) |
| `codeclaw config --exclude "a,b"` | Add excluded projects (appends, never replaces) |
| `codeclaw config --redact "str1,str2"` | Add strings to always redact |
| `codeclaw config --redact-usernames "u1,u2"` | Add usernames to anonymize |
| `codeclaw config --confirm-projects` | Mark project selection confirmed |
| `codeclaw export --no-push` | Export locally only — always do this first |
| `codeclaw export --source codex --no-push` | Export only Codex sessions |
| `codeclaw export --all-projects` | Include all projects (ignore exclusions) |
| `codeclaw export --no-thinking` | Exclude extended thinking blocks |
| `codeclaw confirm ...` | Run PII scan, verify attestations, unlock push |
| `codeclaw confirm --skip-full-name-scan ...` | Skip name scan if user declines |
| `codeclaw export --publish-attestation "..."` | Export and push (requires confirm first) |
| `codeclaw update-skill claude` | Install/update CodeClaw skill for Claude Code |
| `codeclaw synthesize --project my-project` | Generate `CODECLAW.md` knowledge base from synced sessions |
| `codeclaw watch --start` | Start background daemon |
| `codeclaw watch --stop` | Stop background daemon |
| `codeclaw watch --status` | Check daemon status |
| `codeclaw watch --now` | Trigger immediate sync |
| `codeclaw serve` | Start MCP server (requires `pip install codeclaw[mcp]`) |
| `codeclaw install-mcp` | Register CodeClaw as MCP server in Claude Code |

</details>

<details>
<summary><b>What gets exported</b></summary>

| Data | Included | Notes |
|------|----------|-------|
| User messages | ✅ | Full text including voice transcripts |
| Assistant responses | ✅ | Full text output |
| Extended thinking | ✅ | Reasoning traces — opt out with `--no-thinking` |
| Tool calls | ✅ | Tool name + summarized input |
| Tool results | ❌ | Not stored in session logs |
| Token usage | ✅ | Input/output tokens per session |
| Model + metadata | ✅ | Model name, git branch, timestamps |
| Trajectory type | ✅ | Auto-classified: correction_loop, debugging_trace, etc. |
| Code written | ✅ | Captured via Write/Edit tool inputs |
| Error outputs | ✅ | Bash results, stack traces seen by Claude |

### Privacy & Redaction

CodeClaw applies 7 layers of protection before any data leaves your machine:

1. **Path anonymization** — Absolute paths stripped to project-relative
2. **Username hashing** — Your system username + configured handles replaced with stable hashes
3. **Secret detection** — Regex patterns for JWT tokens, API keys (Anthropic, OpenAI, HF, GitHub, AWS, Stripe, etc.), database URLs, private keys, Discord webhooks
4. **Entropy analysis** — Long high-entropy strings flagged and quarantined as potential leaked secrets
5. **Email redaction** — Personal email addresses removed
6. **Custom blocklist** — Add your own strings, domains, and tokens via `codeclaw config --redact`
7. **Pre-truncation redaction** — Secrets in tool inputs redacted *before* any truncation, preventing partial leaks

Sessions with more than 3 redactions are quarantined to `~/.codeclaw/quarantine.jsonl` for manual review — they are never silently dropped.

> ⚠️ **Automated redaction is not foolproof.** Always review your export before publishing. Regex and entropy analysis cannot catch service-specific identifiers, third-party PII, or secrets in unusual formats. The manual confirm step exists for a reason.

Report missed patterns: [github.com/ychampion/codeclaw/issues](https://github.com/ychampion/codeclaw/issues)

</details>

<details>
<summary><b>Data schema</b></summary>

Each line in a session JSONL file is one complete conversation:

```json
{
  "session_id": "abc-123",
  "project": "project",
  "model": "claude-opus-4-6",
  "git_branch": "feat/auth",
  "start_time": "2026-02-25T10:00:00+00:00",
  "end_time": "2026-02-25T10:42:00+00:00",
  "messages": [
    {"role": "user", "content": "Fix the login bug", "timestamp": "..."},
    {
      "role": "assistant",
      "content": "I'll investigate the auth flow.",
      "thinking": "The user wants me to look at the JWT validation...",
      "tool_uses": [{"tool": "Read", "input": "src/auth/middleware.ts"}],
      "timestamp": "..."
    }
  ],
  "metadata": {
    "trajectory_type": "correction_loop",
    "models_used": ["claude-opus-4-6"],
    "push_type": "auto"
  },
  "stats": {
    "user_messages": 5,
    "assistant_messages": 9,
    "tool_uses": 23,
    "input_tokens": 52000,
    "output_tokens": 3400
  }
}
```

Each HF repo also includes a `metadata.json` with aggregate stats: total sessions, trajectory breakdown, model distribution, token counts, and last updated timestamp.

</details>

<details>
<summary><b>MCP Memory Server</b></summary>

CodeClaw includes an optional MCP (Model Context Protocol) server that turns your dataset into **live queryable memory** for any Claude Code session.

```bash
pip install codeclaw[mcp]
codeclaw install-mcp   # registers in ~/.claude/mcp.json automatically
```

Once installed, Claude Code can call these tools mid-session:

| Tool | What it does |
|------|--------------|
| `search_past_solutions(query)` | Full-text search over past sessions — returns top matching conversations |
| `get_project_patterns(project)` | Returns common patterns, frequent tool uses, and error types for a project |
| `get_trajectory_stats()` | Returns trajectory breakdown across all synced sessions |
| `get_session(session_id)` | Returns a full session by ID |
| `find_similar_sessions(context, max_results?)` | Graph-based retrieval over indexed tool/file/error context nodes |
| `refresh_index()` | Rebuild in-memory MCP index after new local sessions land |

This means: when you hit a Prisma error in your project, Claude Code can surface the exact session from 3 months ago where it fixed the same error — with the working solution. Without retraining anything.

Start the server manually:
```bash
codeclaw serve
```

</details>

<details>
<summary><b>Finding datasets on Hugging Face</b></summary>

All CodeClaw datasets are tagged `codeclaw` on Hugging Face.

- **Browse all:** [huggingface.co/datasets?other=codeclaw](https://huggingface.co/datasets?other=codeclaw)
- **Load a specific project subset:**
  ```python
  from datasets import load_dataset
  ds = load_dataset("alice/cc-logs", "my-api", split="train")
  ```
- **Load everything from one user:**
  ```python
  ds = load_dataset("alice/cc-logs", split="train")
  ```
- **Combine datasets from multiple users:**
  ```python
  from datasets import load_dataset, concatenate_datasets
  repos = ["alice/cc-logs", "bob/cc-logs"]
  ds = concatenate_datasets([load_dataset(r, split="train") for r in repos])
  ```
- **Filter by trajectory type for fine-tuning:**
  ```python
  high_signal = ds.filter(
      lambda x: x["metadata"]["trajectory_type"] in ["correction_loop", "debugging_trace"]
  )
  ```

</details>

---

## Roadmap

- [x] Claude Code + Codex session parsing
- [x] Multi-layer secret redaction + entropy analysis
- [x] 6-stage gated export flow with attestation
- [x] Background daemon with filesystem watching
- [x] Trajectory classification (correction_loop, debugging_trace, etc.)
- [x] Per-project HF subfoldering with auto-generated dataset card
- [x] Session deduplication + incremental sync
- [x] CODECLAW.md project knowledge synthesis
- [x] MCP memory server (`codeclaw serve`)
- [ ] MCP memory UX hardening (structured tool payloads, cache refresh, graph similarity retrieval)
- [ ] DPO pair generation from correction_loop sessions
- [ ] Fine-tuning pipeline integration (trl + SFTTrainer) [ optional ]
- [ ] Web dashboard for dataset health monitoring

---



---

## Community

- Contribution guide: [CONTRIBUTING.md](CONTRIBUTING.md)
- Security policy: [SECURITY.md](SECURITY.md)
- Support channels: [SUPPORT.md](SUPPORT.md)
- Code of conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

---

## License

MIT — the dataset you produce is yours. Do what you want with it.
