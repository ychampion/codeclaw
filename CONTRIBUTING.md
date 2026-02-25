# Contributing to CodeClaw

Thanks for your interest in contributing.

## Development Setup

```bash
git clone https://github.com/ychampion/codeclaw.git
cd codeclaw
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell
# .venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e ".[dev]"
```

## Running Checks

Run the full test suite before opening a PR:

```bash
python -m pytest -q
```

Run targeted checks while iterating:

```bash
python -m pytest tests/test_docs_consistency.py -q
python -m pytest tests/test_codeclaw_cli_mcp.py -q
```

## Pull Request Guidelines

- Keep PRs focused on one concern.
- Add or update tests for behavior changes.
- Update docs when command behavior or UX changes.
- Use clear commit messages (imperative style, concise subject).
- Include a short validation summary in the PR description.

## Suggested Commit Message Format

- `feat: <what changed>`
- `fix: <what changed>`
- `refactor: <what changed>`
- `docs: <what changed>`
- `chore: <what changed>`

## Reporting Issues

Use the issue templates and include:

- exact command(s) run
- expected behavior
- actual behavior
- environment details (OS, Python version)
- logs or traceback snippets
