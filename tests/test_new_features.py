import argparse
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codeclaw import __version__
from codeclaw import cli as codeclaw_cli
from codeclaw import daemon
from codeclaw import source_adapters
from codeclaw.cli import finetune, growth
from codeclaw.cli import push_to_huggingface
from codeclaw.cli import export as export_cli
from codeclaw.storage import EncryptionError, decrypt_text, encrypt_text, maybe_encrypt_file


def _extract_json(stdout: str) -> dict:
    start = stdout.find("{")
    assert start >= 0
    return json.loads(stdout[start:])


def test_main_diff_dispatch(monkeypatch):
    called = {"diff": False}

    def _fake_diff(_args):
        called["diff"] = True

    monkeypatch.setattr(codeclaw_cli, "handle_diff", _fake_diff)
    monkeypatch.setattr(sys, "argv", ["codeclaw", "diff"])
    codeclaw_cli.main()
    assert called["diff"] is True


def test_main_console_dispatch(monkeypatch):
    called = {"console": False}

    def _fake_console(_args):
        called["console"] = True

    monkeypatch.setattr(codeclaw_cli, "handle_console", _fake_console)
    monkeypatch.setattr(sys, "argv", ["codeclaw", "console"])
    codeclaw_cli.main()
    assert called["console"] is True


def test_main_tui_dispatch(monkeypatch):
    called = {"tui": False}

    def _fake_tui(_args):
        called["tui"] = True

    monkeypatch.setattr(codeclaw_cli, "handle_tui", _fake_tui)
    monkeypatch.setattr(sys, "argv", ["codeclaw", "tui"])
    codeclaw_cli.main()
    assert called["tui"] is True


def test_main_default_dispatches_tui(monkeypatch):
    called = {"tui": False}

    def _fake_tui(_args):
        called["tui"] = True

    monkeypatch.setattr(codeclaw_cli, "handle_tui", _fake_tui)
    monkeypatch.setattr(sys, "argv", ["codeclaw"])
    codeclaw_cli.main()
    assert called["tui"] is True


def test_main_root_export_flags_still_dispatch_export(monkeypatch):
    called = {"export": False}

    def _fake_export(_args):
        called["export"] = True

    monkeypatch.setattr(codeclaw_cli, "_run_export", _fake_export)
    monkeypatch.setattr(sys, "argv", ["codeclaw", "--no-push"])
    codeclaw_cli.main()
    assert called["export"] is True


def test_main_version_flag_outputs_package_version(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["codeclaw", "--version"])
    with pytest.raises(SystemExit) as exc:
        codeclaw_cli.main()
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_main_short_version_flag_outputs_package_version(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["codeclaw", "-V"])
    with pytest.raises(SystemExit) as exc:
        codeclaw_cli.main()
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_python_m_codeclaw_version_smoke():
    result = subprocess.run(
        [sys.executable, "-m", "codeclaw", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert __version__ in result.stdout


def test_finetune_requires_experimental(capsys):
    with pytest.raises(SystemExit):
        finetune.handle_finetune(argparse.Namespace(experimental=False, dataset=None, output=None))
    payload = _extract_json(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "experimental" in payload["error"].lower()


def test_finetune_experimental_payload(capsys):
    finetune.handle_finetune(
        argparse.Namespace(experimental=True, dataset="input.jsonl", output=".tmp/out")
    )
    payload = _extract_json(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["experimental"] is True
    assert payload["pipeline"]["framework"] == "unsloth-qlora"


def test_stats_skill_included(monkeypatch, capsys):
    monkeypatch.setattr(
        growth,
        "load_config",
        lambda: {
            "source": "both",
            "synced_session_ids": [],
            "published_dedupe_index": {"abc": {"session_id": "s1"}},
            "dataset_versioning_mode": "immutable_snapshots",
            "dataset_latest_version": "v1",
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
            {
                "session_id": "s-1",
                "start_time": "2026-02-01T00:00:00+00:00",
                "messages": [{"role": "user", "content": "fix bug"}],
                "stats": {"input_tokens": 100, "output_tokens": 20},
                "trajectory_type": "debugging_trace",
            }
        ],
    )
    growth.handle_stats(argparse.Namespace(source="auto", skill=True))
    payload = _extract_json(capsys.readouterr().out)
    assert "skill" in payload
    assert payload["dataset_versioning"]["latest_version"] == "v1"


def test_encrypt_decrypt_roundtrip():
    text = '{"sample":"data"}'
    encrypted = encrypt_text(text, config={"encryption_key_ref": None})
    # If crypto deps are unavailable, helper falls back to plaintext.
    decrypted = decrypt_text(encrypted, config={"encryption_key_ref": None})
    if encrypted == text:
        assert decrypted == text
    else:
        assert decrypted == text


def test_doctor_includes_platform_checks(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cfg_file = tmp_path / ".codeclaw" / "config.json"
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(growth, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(growth, "load_config", lambda: {})
    monkeypatch.setattr(growth, "_has_session_sources", lambda _source: False)
    monkeypatch.setattr(growth, "get_hf_username", lambda: None)

    with pytest.raises(SystemExit):
        growth.handle_doctor(argparse.Namespace(source="auto"))
    payload = _extract_json(capsys.readouterr().out)
    assert "platform_checks" in payload
    assert "adapter_diagnostics" in payload["platform_checks"]


def test_external_adapter_discovery(monkeypatch, tmp_path):
    home = tmp_path / "home"
    cursor_dir = home / ".cursor" / "sessions" / "project-a"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    (cursor_dir / "session-1.jsonl").write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")

    monkeypatch.setattr(source_adapters, "_home", lambda: home)
    projects = source_adapters.discover_external_projects()
    assert any(project["source"] == "cursor" for project in projects)


def test_push_to_hf_writes_version_manifest(monkeypatch, tmp_path):
    jsonl_path = tmp_path / "data.jsonl"
    jsonl_path.write_text(
        '{"session_id":"abc123","project":"proj","model":"m","messages":[{"role":"user","content":"hi"}],"stats":{}}\n',
        encoding="utf-8",
    )
    meta = {"exported_session_ids": ["abc123"], "sessions": 1, "projects": ["proj"], "redactions": 0}
    saved: dict = {}

    monkeypatch.setattr(
        "codeclaw.cli.export.load_config",
        lambda: {
            "repo_private": True,
            "published_dedupe_index": {},
            "dataset_versioning_mode": "immutable_snapshots",
            "synced_session_ids": [],
        },
    )
    monkeypatch.setattr("codeclaw.cli.export.save_config", lambda cfg: saved.update(cfg))

    mock_api = MagicMock()
    mock_api.whoami.return_value = {"name": "alice"}
    mock_api.list_repo_files.return_value = []
    mock_api.create_commit.side_effect = RuntimeError("commit API unavailable in mock")
    mock_hf_module = MagicMock()
    mock_hf_module.HfApi.return_value = mock_api

    with patch.dict("sys.modules", {"huggingface_hub": mock_hf_module}):
        push_to_huggingface(jsonl_path, "alice/repo", meta)

    uploaded_paths = [call.kwargs["path_in_repo"] for call in mock_api.upload_file.call_args_list]
    assert any(path.startswith("versions/v") for path in uploaded_paths)
    assert "versions/latest.json" in uploaded_paths
    assert saved.get("dataset_latest_version")
    assert saved.get("published_dedupe_index")


def test_push_to_hf_uses_batched_commit_api(monkeypatch, tmp_path):
    jsonl_path = tmp_path / "data.jsonl"
    jsonl_path.write_text(
        "\n".join(
            [
                '{"session_id":"s1","project":"proj","model":"m","messages":[{"role":"user","content":"hi"}],"stats":{}}',
                '{"session_id":"s2","project":"proj","model":"m","messages":[{"role":"user","content":"hi"}],"stats":{}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    saved: dict = {}

    monkeypatch.setattr(
        "codeclaw.cli.export.load_config",
        lambda: {
            "repo_private": True,
            "published_dedupe_index": {},
            "dataset_versioning_mode": "immutable_snapshots",
            "synced_session_ids": [],
            "hf_commit_max_operations": 1,
        },
    )
    monkeypatch.setattr("codeclaw.cli.export.save_config", lambda cfg: saved.update(cfg))

    mock_api = MagicMock()
    mock_api.whoami.return_value = {"name": "alice"}
    mock_api.list_repo_files.return_value = []

    class _CommitOperationAdd:
        def __init__(self, path_in_repo, path_or_fileobj):
            self.path_in_repo = path_in_repo
            self.path_or_fileobj = path_or_fileobj

    class _HFModule:
        HfApi = lambda self=None: mock_api
        CommitOperationAdd = _CommitOperationAdd

    with patch.dict("sys.modules", {"huggingface_hub": _HFModule()}):
        push_to_huggingface(
            jsonl_path,
            "alice/repo",
            {"exported_session_ids": ["s1", "s2"], "sessions": 2, "projects": ["proj"], "redactions": 0},
        )

    assert mock_api.create_commit.call_count >= 2
    commit_paths = []
    for call in mock_api.create_commit.call_args_list:
        for op in call.kwargs["operations"]:
            commit_paths.append(op.path_in_repo)
    assert any(path.startswith("data/proj/train-") for path in commit_paths)
    assert "metadata.json" in commit_paths
    assert "README.md" in commit_paths
    assert saved.get("dataset_latest_version")


def test_scan_for_text_occurrences_reports_encryption_error(monkeypatch, tmp_path):
    file_path = tmp_path / "export.jsonl"
    file_path.write_text("CODECLAW_ENCRYPTED_V1:abc", encoding="utf-8")

    monkeypatch.setattr(
        export_cli,
        "read_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(EncryptionError("bad key")),
    )
    payload = export_cli._scan_for_text_occurrences(file_path, "alice")
    assert payload["match_count"] == 0
    assert payload["error"] == "bad key"
    assert "doctor" in payload["hint"].lower()


def test_push_to_hf_exits_on_encryption_error(monkeypatch, tmp_path, capsys):
    jsonl_path = tmp_path / "data.jsonl"
    jsonl_path.write_text("CODECLAW_ENCRYPTED_V1:abc", encoding="utf-8")

    monkeypatch.setattr(
        "codeclaw.cli.export.load_config",
        lambda: {"repo_private": True, "published_dedupe_index": {}, "synced_session_ids": []},
    )

    mock_api = MagicMock()
    mock_api.whoami.return_value = {"name": "alice"}
    mock_hf_module = MagicMock()
    mock_hf_module.HfApi.return_value = mock_api

    monkeypatch.setattr(
        "codeclaw.cli.export._read_sessions_from_jsonl",
        lambda _path: (_ for _ in ()).throw(EncryptionError("missing key")),
    )

    with patch.dict("sys.modules", {"huggingface_hub": mock_hf_module}):
        with pytest.raises(SystemExit):
            push_to_huggingface(
                jsonl_path,
                "alice/repo",
                {"exported_session_ids": [], "sessions": 0, "projects": [], "redactions": 0},
            )

    captured = capsys.readouterr()
    assert "Error reading encrypted export file" in captured.err


def test_export_dry_run_bypasses_confirm_gate(monkeypatch, capsys):
    monkeypatch.setattr(
        "codeclaw.cli.export.load_config",
        lambda: {"source": "both", "projects_confirmed": True, "stage": "configure"},
    )
    monkeypatch.setattr("codeclaw.cli.export._has_session_sources", lambda _src: True)
    monkeypatch.setattr(
        "codeclaw.cli.export.discover_projects",
        lambda: [{"display_name": "proj", "dir_name": "proj", "session_count": 3, "total_size_bytes": 1024, "source": "claude"}],
    )
    monkeypatch.setattr("codeclaw.cli.export.get_hf_username", lambda: None)
    monkeypatch.setattr(
        "codeclaw.cli.export.export_to_jsonl",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("export_to_jsonl should not run in dry-run")),
    )
    monkeypatch.setattr(sys, "argv", ["codeclaw", "export", "--dry-run"])
    codeclaw_cli.main()
    payload = _extract_json(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["estimated_sessions"] == 3
    assert payload["would_publish"] is True


def test_maybe_encrypt_file_auto_initializes_key(monkeypatch, tmp_path):
    file_path = tmp_path / "export.jsonl"
    file_path.write_text('{"x":1}\n', encoding="utf-8")
    config = {"encryption_enabled": True, "encryption_key_ref": None}

    monkeypatch.setattr("codeclaw.storage.encryption_status", lambda _cfg=None: {"key_present": False})
    monkeypatch.setattr(
        "codeclaw.storage.ensure_encryption_key",
        lambda _cfg=None: (True, "file:default", "file"),
    )
    monkeypatch.setattr(
        "codeclaw.storage.encrypt_text",
        lambda plain, config=None: "CODECLAW_ENCRYPTED_V1:token",
    )

    assert maybe_encrypt_file(file_path, config=config) is True
    assert config["encryption_key_ref"] == "file:default"


def test_is_pid_running_handles_system_error(monkeypatch):
    monkeypatch.setattr(daemon.os, "kill", lambda _pid, _sig: (_ for _ in ()).throw(SystemError("bad pid")))
    assert daemon._is_pid_running(1234) is False
