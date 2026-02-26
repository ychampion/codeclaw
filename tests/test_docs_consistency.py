from pathlib import Path
import re
import subprocess
import sys
import tomllib

from codeclaw import __version__


REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
DOC_SKILL = REPO_ROOT / "docs" / "SKILL.md"
CLAUDE_SKILL = REPO_ROOT / ".claude" / "skills" / "codeclaw" / "SKILL.md"
EXPORT_FILE = REPO_ROOT / "codeclaw" / "cli" / "export.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _iter_text_files() -> list[Path]:
    skip_dirs = {
        ".git",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "__pycache__",
        "dist",
        "codeclaw.egg-info",
    }
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz"}:
            continue
        files.append(path)
    return files


def test_no_legacy_brand_terms_in_core_docs():
    legacy_a = "open" + "claw"
    legacy_b = "data" + "claw"
    for path in (README, DOC_SKILL, CLAUDE_SKILL, EXPORT_FILE):
        text = _read(path).lower()
        assert legacy_a not in text, f"legacy term found in {path}"
        assert legacy_b not in text, f"legacy term found in {path}"


def test_no_legacy_brand_terms_repo_wide():
    legacy_terms = ("open" + "claw", "data" + "claw", "code" + "law")
    for path in _iter_text_files():
        try:
            text = _read(path).lower()
        except UnicodeDecodeError:
            continue
        for term in legacy_terms:
            assert term not in text, f"legacy term '{term}' found in {path}"


def test_only_codeclaw_skill_path_exists():
    skill_dir = REPO_ROOT / ".claude" / "skills"
    skill_files = sorted(path.relative_to(skill_dir) for path in skill_dir.rglob("SKILL.md"))
    assert skill_files == [Path("codeclaw") / "SKILL.md"]


def test_readme_has_no_old_command_typos():
    typo = "code" + "law"
    assert typo not in _read(README).lower()


def test_readme_command_table_uses_implemented_subcommands():
    supported = {
        "status",
        "prep",
        "confirm",
        "diff",
        "list",
        "doctor",
        "stats",
        "share",
        "console",
        "tui",
        "finetune",
        "projects",
        "reset",
        "update-skill",
        "synthesize",
        "watch",
        "setup",
        "config",
        "serve",
        "install-mcp",
        "export",
    }
    commands: list[str] = []
    for line in _read(README).splitlines():
        line = line.strip()
        if line.startswith("| `codeclaw "):
            commands.append(line.split("`")[1])

    assert commands, "README command table was not found"
    for cmd in commands:
        parts = cmd.split()
        assert len(parts) >= 2, f"malformed command row: {cmd}"
        assert parts[0] == "codeclaw"
        assert parts[1] in supported, f"unsupported command in README: {cmd}"


def test_readme_command_table_matches_cli_help_surface():
    result = subprocess.run(
        [sys.executable, "-m", "codeclaw", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    help_text = result.stdout

    command_groups = re.findall(r"\{([^}]+)\}", help_text)
    group = next(
        (
            item
            for item in command_groups
            if "prep" in item and "status" in item and "export" in item and "tui" in item
        ),
        None,
    )
    assert group, "failed to parse CLI command set from `python -m codeclaw --help`"
    cli_commands = {entry.strip() for entry in group.split(",") if entry.strip()}

    readme_commands: set[str] = set()
    for line in _read(README).splitlines():
        line = line.strip()
        if not line.startswith("| `codeclaw "):
            continue
        command = line.split("`")[1]
        parts = command.split()
        if len(parts) >= 2:
            readme_commands.add(parts[1])

    missing = sorted(command for command in cli_commands if command not in readme_commands)
    assert not missing, f"README command table missing CLI subcommands: {missing}"


def test_readme_does_not_reference_removed_update_kb_command():
    removed = "update" + "-kb"
    assert removed not in _read(README)


def test_readme_has_no_public_roadmap_section():
    assert "## Roadmap" not in _read(README)


def test_codeclaw_json_marker_consistency():
    legacy_marker = "DATA" + "CLAW_JSON"
    for path in (DOC_SKILL, CLAUDE_SKILL, EXPORT_FILE):
        text = _read(path)
        assert "---CODECLAW_JSON---" in text, f"missing marker in {path}"
        assert legacy_marker not in text, f"legacy marker found in {path}"


def test_package_version_matches_pyproject():
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = pyproject["project"]["version"]
    assert declared == __version__
