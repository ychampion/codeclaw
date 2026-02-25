import argparse
import json
import signal
import sys
from types import SimpleNamespace

import pytest

from codeclaw import cli as codeclaw_cli
from codeclaw.cli import growth
from codeclaw.cli._helpers import normalize_repo_id
from codeclaw.daemon import trigger_sync_now


def _extract_json(stdout: str) -> dict:
    start = stdout.find("{")
    assert start >= 0
    return json.loads(stdout[start:])


def test_normalize_repo_id_accepts_hf_dataset_url():
    assert (
        normalize_repo_id("https://huggingface.co/datasets/alice/my-dataset/")
        == "alice/my-dataset"
    )


def test_setup_yes_starts_watcher_and_sets_connected_current(monkeypatch, capsys):
    saved: dict = {}
    monkeypatch.setattr("codeclaw.cli.setup.load_config", lambda: {})
    monkeypatch.setattr("codeclaw.cli.setup.save_config", lambda cfg: saved.update(cfg))
    monkeypatch.setattr("codeclaw.cli.setup.get_hf_username", lambda: "alice")
    monkeypatch.setattr(
        "codeclaw.cli.setup.discover_projects",
        lambda: [
            {"display_name": "proj-a", "source": "claude"},
            {"display_name": "proj-b", "source": "claude"},
        ],
    )
    monkeypatch.setattr(
        "codeclaw.cli.setup.detect_current_project",
        lambda: {"display_name": "proj-b"},
    )
    monkeypatch.setattr("codeclaw.daemon.start_daemon", lambda: {"running": True, "pid": 456})
    monkeypatch.setattr(sys, "argv", ["codeclaw", "setup", "--yes"])

    codeclaw_cli.main()
    payload = _extract_json(capsys.readouterr().out)

    assert payload["pid"] == 456
    assert payload["projects"]["connected"] == ["proj-b"]
    assert saved["connected_projects"] == ["proj-b"]
    assert saved["source"] == "both"


def test_projects_connect_and_disconnect(monkeypatch, capsys):
    saved: dict = {}
    base_config = {"source": "both", "connected_projects": ["proj-a"]}
    monkeypatch.setattr("codeclaw.cli.projects.load_config", lambda: dict(base_config))
    monkeypatch.setattr("codeclaw.cli.projects.save_config", lambda cfg: saved.update(cfg))
    monkeypatch.setattr(
        "codeclaw.cli.projects.discover_projects",
        lambda: [
            {"display_name": "proj-a", "source": "claude"},
            {"display_name": "proj-b", "source": "claude"},
        ],
    )
    monkeypatch.setattr(sys, "argv", ["codeclaw", "projects", "--connect", "proj-b", "--disconnect", "proj-a"])

    codeclaw_cli.main()
    payload = _extract_json(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["connected_projects"] == ["proj-b"]
    assert saved["connected_projects"] == ["proj-b"]


def test_doctor_handles_project_discovery_failure(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(growth, "CONFIG_FILE", tmp_path / ".codeclaw" / "config.json")
    monkeypatch.setattr(growth, "load_config", lambda: {})
    monkeypatch.setattr(growth, "_has_session_sources", lambda _source: True)
    monkeypatch.setattr(growth, "discover_projects", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(growth, "get_hf_username", lambda: None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    with pytest.raises(SystemExit):
        growth.handle_doctor(argparse.Namespace(source="auto"))

    payload = _extract_json(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["checks"]["project_discovery"]["error"].startswith("RuntimeError: boom")


def test_trigger_sync_now_uses_standalone_fallback_without_sigusr1(monkeypatch):
    fake_signal = SimpleNamespace(SIGTERM=signal.SIGTERM, SIGINT=signal.SIGINT)
    monkeypatch.setattr("codeclaw.daemon.signal", fake_signal)
    monkeypatch.setattr("codeclaw.daemon._setup_logger", lambda: object())
    monkeypatch.setattr("codeclaw.daemon._read_pid", lambda: 999)
    monkeypatch.setattr("codeclaw.daemon._poll_once", lambda _logger: 3)

    payload = trigger_sync_now()

    assert payload["triggered"] is True
    assert payload["mode"] == "standalone_fallback"
    assert payload["sessions"] == 3
    assert payload["running_pid"] == 999
