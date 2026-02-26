import argparse
import json
import sys
from pathlib import Path

import pytest

from codeclaw import cli as codeclaw_cli
from codeclaw.cli import growth


def _extract_json(stdout: str) -> dict:
    start = stdout.find("{")
    assert start >= 0
    return json.loads(stdout[start:])


def test_main_doctor_dispatch(monkeypatch):
    called = {"doctor": False}

    def _fake_doctor(_args):
        called["doctor"] = True

    monkeypatch.setattr(codeclaw_cli, "handle_doctor", _fake_doctor)
    monkeypatch.setattr(sys, "argv", ["codeclaw", "doctor"])
    codeclaw_cli.main()
    assert called["doctor"] is True


def test_main_stats_dispatch(monkeypatch):
    called = {"stats": False}

    def _fake_stats(_args):
        called["stats"] = True

    monkeypatch.setattr(codeclaw_cli, "handle_stats", _fake_stats)
    monkeypatch.setattr(sys, "argv", ["codeclaw", "stats"])
    codeclaw_cli.main()
    assert called["stats"] is True


def test_main_share_dispatch(monkeypatch):
    called = {"share": False}

    def _fake_share(_args):
        called["share"] = True

    monkeypatch.setattr(codeclaw_cli, "handle_share", _fake_share)
    monkeypatch.setattr(sys, "argv", ["codeclaw", "share"])
    codeclaw_cli.main()
    assert called["share"] is True


def test_doctor_reports_failed_checks(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(growth, "CONFIG_FILE", tmp_path / ".codeclaw" / "config.json")
    monkeypatch.setattr(growth, "load_config", lambda: {})
    monkeypatch.setattr(growth, "_has_session_sources", lambda _source: False)
    monkeypatch.setattr(growth, "discover_projects", lambda: [])
    monkeypatch.setattr(growth, "get_hf_username", lambda: None)

    with pytest.raises(SystemExit):
        growth.handle_doctor(argparse.Namespace(source="auto"))

    payload = _extract_json(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["checks"]["huggingface_auth"]["ok"] is False
    assert payload["checks"]["mcp_registration"]["ok"] is False
    assert "runtime" in payload
    assert payload["runtime"]["module_version"]


def test_doctor_passes_when_setup_is_ready(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cfg_file = tmp_path / ".codeclaw" / "config.json"
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(growth, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(growth, "load_config", lambda: {})
    monkeypatch.setattr(growth, "_has_session_sources", lambda _source: True)
    monkeypatch.setattr(
        growth,
        "discover_projects",
        lambda: [{"display_name": "proj", "dir_name": "proj", "source": "claude"}],
    )
    monkeypatch.setattr(growth, "get_hf_username", lambda: "alice")

    mcp_path = tmp_path / ".claude" / "mcp.json"
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "codeclaw": {
                        "command": sys.executable,
                        "args": ["-m", "codeclaw.mcp_server", "--serve"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    growth.handle_doctor(argparse.Namespace(source="auto"))
    payload = _extract_json(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["checks"]["project_discovery"]["project_count"] == 1
    assert payload["runtime"]["module_version"]


def test_doctor_path_mismatch_adds_next_steps(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cfg_file = tmp_path / ".codeclaw" / "config.json"
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(growth, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(growth, "load_config", lambda: {})
    monkeypatch.setattr(growth, "_has_session_sources", lambda _source: True)
    monkeypatch.setattr(growth, "discover_projects", lambda: [])
    monkeypatch.setattr(growth, "get_hf_username", lambda: "alice")
    monkeypatch.setattr(
        growth,
        "_runtime_diagnostics",
        lambda: {
            "module_version": "0.4.4",
            "python_executable": str(tmp_path / "python.exe"),
            "codeclaw_on_path": str(tmp_path / "other" / "codeclaw.exe"),
            "codeclaw_on_path_in_python_env": False,
            "path_hint": "mismatch",
        },
    )

    with pytest.raises(SystemExit):
        growth.handle_doctor(argparse.Namespace(source="auto"))

    payload = _extract_json(capsys.readouterr().out)
    assert payload["runtime"]["path_hint"] == "mismatch"
    steps = payload["next_steps"]
    assert any("python -m codeclaw --version" in step for step in steps)


def test_stats_aggregates_session_and_lifetime_metrics(monkeypatch, capsys):
    monkeypatch.setattr(
        growth,
        "load_config",
        lambda: {
            "source": "both",
            "synced_session_ids": ["s-1"],
            "stats_total_exports": 3,
            "stats_total_publishes": 2,
            "stats_total_exported_sessions": 12,
            "stats_total_redactions": 9,
            "stats_total_input_tokens": 1000,
            "stats_total_output_tokens": 500,
        },
    )
    monkeypatch.setattr(
        growth,
        "discover_projects",
        lambda: [{"display_name": "proj", "dir_name": "proj", "source": "claude"}],
    )
    monkeypatch.setattr(
        growth,
        "parse_project_sessions",
        lambda *a, **kw: [
            {"session_id": "s-1", "stats": {"input_tokens": 100, "output_tokens": 40}},
            {"session_id": "s-2", "stats": {"input_tokens": 50, "output_tokens": 10}},
        ],
    )

    growth.handle_stats(argparse.Namespace(source="auto"))
    payload = _extract_json(capsys.readouterr().out)

    assert payload["summary"]["sessions_available"] == 2
    assert payload["summary"]["sessions_captured"] == 1
    assert payload["summary"]["sessions_pending"] == 1
    assert payload["summary"]["redactions_made"] == 9
    assert payload["tokens"]["captured_total"] == 140
    assert payload["lifetime"]["exports"] == 3


def test_share_publish_requires_attestation(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        growth,
        "load_config",
        lambda: {
            "source": "both",
            "excluded_projects": [],
            "disabled_projects": [],
            "redact_strings": [],
            "redact_usernames": [],
        },
    )
    monkeypatch.setattr(growth, "_has_session_sources", lambda _source: True)
    monkeypatch.setattr(
        growth,
        "discover_projects",
        lambda: [{"display_name": "proj", "dir_name": "proj", "source": "claude"}],
    )
    monkeypatch.setattr(
        growth,
        "export_to_jsonl",
        lambda **kwargs: {
            "sessions": 1,
            "redactions": 0,
            "models": {"claude-sonnet": 1},
            "projects": ["proj"],
            "total_input_tokens": 10,
            "total_output_tokens": 5,
            "exported_at": "2026-02-25T00:00:00+00:00",
            "exported_session_ids": ["s-1"],
        },
    )
    monkeypatch.setattr(growth, "get_hf_username", lambda: "alice")

    with pytest.raises(SystemExit):
        growth.handle_share(
            argparse.Namespace(
                output=tmp_path / "share.jsonl",
                repo=None,
                source="auto",
                all_projects=False,
                no_thinking=False,
                publish=True,
                publish_attestation=None,
            )
        )

    payload = _extract_json(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "attestation" in payload["error"].lower()


def test_share_publish_updates_config_and_pushes(monkeypatch, tmp_path, capsys):
    saved: dict = {}
    push_calls: list[tuple[str, str]] = []
    base_config = {
        "source": "both",
        "excluded_projects": [],
        "disabled_projects": [],
        "redact_strings": [],
        "redact_usernames": [],
    }

    monkeypatch.setattr(growth, "load_config", lambda: dict(base_config))
    monkeypatch.setattr(growth, "save_config", lambda cfg: saved.update(cfg))
    monkeypatch.setattr(growth, "_has_session_sources", lambda _source: True)
    monkeypatch.setattr(
        growth,
        "discover_projects",
        lambda: [{"display_name": "proj", "dir_name": "proj", "source": "claude"}],
    )
    monkeypatch.setattr(
        growth,
        "export_to_jsonl",
        lambda **kwargs: {
            "sessions": 2,
            "redactions": 3,
            "models": {"claude-sonnet": 2},
            "projects": ["proj"],
            "total_input_tokens": 200,
            "total_output_tokens": 80,
            "exported_at": "2026-02-25T00:00:00+00:00",
            "exported_session_ids": ["s-1", "s-2"],
        },
    )
    monkeypatch.setattr(growth, "get_hf_username", lambda: "alice")
    monkeypatch.setattr(growth, "_validate_publish_attestation", lambda text: (text, None))

    def _fake_push(path, repo_id, _meta):
        push_calls.append((str(path), repo_id))

    monkeypatch.setattr(growth, "push_to_huggingface", _fake_push)

    growth.handle_share(
        argparse.Namespace(
            output=tmp_path / "share.jsonl",
            repo=None,
            source="auto",
            all_projects=False,
            no_thinking=False,
            publish=True,
            publish_attestation="User explicitly approved publishing to Hugging Face on 2026-02-25.",
        )
    )

    payload = _extract_json(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["published"] is True
    assert payload["dataset_card_updated"] is True
    assert push_calls[0][1] == "alice/my-personal-codex-data"
    assert saved["stage"] == "done"
    assert saved["stats_total_publishes"] == 1
