from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
DOC_SKILL = REPO_ROOT / "docs" / "SKILL.md"
CLAUDE_SKILL = REPO_ROOT / ".claude" / "skills" / "codeclaw" / "SKILL.md"
EXPORT_FILE = REPO_ROOT / "codeclaw" / "cli" / "export.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_no_legacy_brand_terms_in_core_docs():
    legacy_a = "open" + "claw"
    legacy_b = "data" + "claw"
    for path in (README, DOC_SKILL, CLAUDE_SKILL, EXPORT_FILE):
        text = _read(path).lower()
        assert legacy_a not in text, f"legacy term found in {path}"
        assert legacy_b not in text, f"legacy term found in {path}"


def test_readme_has_no_old_command_typos():
    typo = "code" + "law"
    assert typo not in _read(README).lower()


def test_readme_command_table_uses_implemented_subcommands():
    supported = {
        "status",
        "prep",
        "confirm",
        "list",
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
