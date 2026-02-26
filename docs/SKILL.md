---
name: codeclaw
description: >
  Export Claude Code and Codex conversation history to Hugging Face as structured training data.
  Use when the user asks about exporting conversations, uploading to Hugging Face,
  configuring codeclaw, reviewing PII/secrets in exports, or managing their dataset.
allowed-tools: Bash(codeclaw *), Bash(huggingface-cli login *), Bash(pip install codeclaw*), Bash(grep *)
---

<!-- codeclaw-begin -->

# codeclaw Skill

## THE RULE

**Every `codeclaw` command outputs `next_steps`. FOLLOW THEM.**

Do not memorize the flow. Do not skip steps. Do not improvise.
Run the command -> read the output -> follow `next_steps`. That's it.

The CLI tracks your stage (1-4: auth -> configure -> review -> done).
`codeclaw export` (push) is **gated** -- you must run `codeclaw confirm` first or it will refuse.

## Getting Started

Run `codeclaw status` (or `codeclaw prep` for full details) and follow the `next_steps`.

## Output Format

- `codeclaw prep`, `codeclaw config`, `codeclaw status`, and `codeclaw confirm` output pure JSON
- `codeclaw export` outputs human-readable text followed by `---CODECLAW_JSON---` and a JSON block
- Always parse the JSON and act on `next_steps`

Key fields:
- `stage` / `stage_number` / `total_stages` -- where you are
- `next_steps` -- follow these in order
- `next_command` -- the single most important command to run next (null if user input needed first)

## PII Audit (Stage 3)

After `codeclaw export --no-push`, follow the `next_steps` in the JSON output. The flow is:

1. **Ask the user their full name** -- then grep the export for it
2. **Run the pii_commands** from the JSON output and review results with the user
3. **Ask the user what else to look for** -- company names, client names, private URLs, other people's names, custom domains
4. **Deep manual scan** -- sample ~20 sessions (beginning, middle, end) and look for anything sensitive the regex missed
5. **Fix and re-export** if anything found: `codeclaw config --redact "string"` then `codeclaw export --no-push`
6. **Run `codeclaw confirm` with text attestations** -- pass `--full-name`, `--attest-full-name`, `--attest-sensitive`, and `--attest-manual-scan`. It runs PII scan, verifies attestations, shows project breakdown, and unlocks pushing.
7. **Push only after explicit user confirmation**: `codeclaw export --publish-attestation "User explicitly approved publishing to Hugging Face."`

## Commands Reference

```bash
codeclaw status                            # Show current stage and next steps (JSON)
codeclaw prep                              # Discover projects, check HF auth (JSON)
codeclaw setup                             # Guided onboarding (HF, dataset, projects, MCP, watcher)
codeclaw prep --source both                # Claude + Codex sessions
codeclaw prep --source codex               # Only Codex sessions
codeclaw prep --source claude              # Only Claude Code sessions
codeclaw confirm --full-name "NAME" --attest-full-name "..." --attest-sensitive "..." --attest-manual-scan "..." # Scan PII, verify attestations, unlock pushing (JSON)
codeclaw confirm --file /path/to/file.jsonl --full-name "NAME" --attest-full-name "..." --attest-sensitive "..." --attest-manual-scan "..." # Confirm a specific export file
codeclaw list                              # List all projects with exclusion status
codeclaw projects                          # Show connected project scope
codeclaw projects --connect "proj1,proj2" # Connect specific projects
codeclaw projects --use-current            # Connect only current project
codeclaw diff                              # Show what redaction pipeline will remove before confirm
codeclaw stats --skill                     # Show growth-oriented trajectory metrics
codeclaw list --source both                # List Claude + Codex projects
codeclaw list --source codex               # List only Codex projects
codeclaw config                            # Show current config
codeclaw config --repo user/my-personal-codex-data  # Set HF repo
codeclaw config --source both              # REQUIRED source scope: claude|codex|both
codeclaw config --exclude "a,b"            # Add excluded projects (appends)
codeclaw config --redact "str1,str2"       # Add strings to redact (appends)
codeclaw config --redact-usernames "u1,u2" # Add usernames to anonymize (appends)
codeclaw config --confirm-projects         # Mark project selection as confirmed
codeclaw config --encryption status        # Check encryption-at-rest state
codeclaw export --publish-attestation "..." # Export and push (requires codeclaw confirm first)
codeclaw export --no-push                  # Export locally only
codeclaw export --source both --no-push    # Export Claude + Codex sessions
codeclaw export --source codex --no-push   # Export only Codex sessions
codeclaw export --source claude --no-push  # Export only Claude Code sessions
codeclaw export --all-projects             # Include everything (ignore exclusions)
codeclaw export --no-thinking              # Exclude extended thinking blocks
codeclaw export -o /path/to/file.jsonl     # Custom output path
codeclaw watch --status                    # Watcher status with transparency fields
codeclaw watch --logs --follow             # Stream daemon logs
codeclaw watch --monitor --follow          # Live watcher monitor (status + recent logs)
codeclaw watch --pause                     # Pause watcher without stopping process
codeclaw watch --resume                    # Resume watcher polling
codeclaw watch --switch-project "project"  # Scope watcher to one connected project
codeclaw console --source both             # Open interactive slash-command console
codeclaw update-skill claude               # Install/update the codeclaw skill for Claude Code
```

## Gotchas

- **Never run bare `huggingface-cli login`** -- it's interactive and will hang. Always use `--token`.
- **`--exclude`, `--redact`, `--redact-usernames` APPEND** -- they never overwrite. Safe to call repeatedly.
- **Source selection is REQUIRED before export** -- explicitly set `codeclaw config --source claude|codex|both` (or pass `--source ...` on export).
- **`codeclaw prep` outputs pure JSON** -- parse it directly.
- **Always export with `--no-push` first** -- review before publishing.
- **`codeclaw export` (push) requires `codeclaw confirm` first** -- it will refuse otherwise. Re-exporting with `--no-push` resets this.
- **PII audit is critical** -- automated redaction is not foolproof.
- **Large exports take time** -- 500+ sessions may take 1-3 minutes. Use a generous timeout.

## Prerequisite

`command -v codeclaw >/dev/null 2>&1 && echo "codeclaw: installed" || echo "NOT INSTALLED -- run: pip install codeclaw"`

<!-- codeclaw-end -->
