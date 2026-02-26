import argparse
import json

import pytest

from codeclaw.cli import reset as reset_cli


def _extract_json(stdout: str) -> dict:
    start = stdout.find("{")
    assert start >= 0
    return json.loads(stdout[start:])


def test_reset_all_removes_local_setup_and_mcp_entry(monkeypatch, tmp_path, capsys):
    home = tmp_path / "home"
    codeclaw_dir = home / ".codeclaw"
    codeclaw_dir.mkdir(parents=True, exist_ok=True)
    config_file = codeclaw_dir / "config.json"
    config_file.write_text('{"repo":"alice/data"}', encoding="utf-8")
    (codeclaw_dir / "daemon.log").write_text("log", encoding="utf-8")
    (codeclaw_dir / "daemon_state.json").write_text("{}", encoding="utf-8")
    (codeclaw_dir / "archive").mkdir(parents=True, exist_ok=True)
    (codeclaw_dir / "logs").mkdir(parents=True, exist_ok=True)

    mcp_path = home / ".claude" / "mcp.json"
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "codeclaw": {"command": "python", "args": ["-m", "codeclaw.mcp_server"]},
                    "other": {"command": "node", "args": ["server.js"]},
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(reset_cli.Path, "home", lambda: home)
    monkeypatch.setattr(reset_cli, "CONFIG_FILE", config_file)
    monkeypatch.setattr("codeclaw.daemon.stop_daemon", lambda: {"running": False, "stopped": True})

    reset_cli.handle_reset(argparse.Namespace(all=True, config=False, state=False, mcp=False, yes=True))
    payload = _extract_json(capsys.readouterr().out)

    assert payload["ok"] is True
    assert not config_file.exists()
    assert not (codeclaw_dir / "daemon.log").exists()
    assert not (codeclaw_dir / "archive").exists()

    updated_mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert "codeclaw" not in updated_mcp["mcpServers"]
    assert "other" in updated_mcp["mcpServers"]


def test_reset_cancelled_without_yes(monkeypatch, tmp_path, capsys):
    home = tmp_path / "home"
    monkeypatch.setattr(reset_cli.Path, "home", lambda: home)
    monkeypatch.setattr(reset_cli, "CONFIG_FILE", home / ".codeclaw" / "config.json")
    monkeypatch.setattr(reset_cli, "_prompt_yes_no", lambda *_args, **_kwargs: False)

    with pytest.raises(SystemExit) as exc:
        reset_cli.handle_reset(argparse.Namespace(all=True, config=False, state=False, mcp=False, yes=False))
    assert exc.value.code == 1

    payload = _extract_json(capsys.readouterr().out)
    assert payload["aborted"] is True
    assert payload["ok"] is False


def test_reset_reports_invalid_mcp_json(monkeypatch, tmp_path, capsys):
    home = tmp_path / "home"
    mcp_path = home / ".claude" / "mcp.json"
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text("{ invalid json", encoding="utf-8")

    monkeypatch.setattr(reset_cli.Path, "home", lambda: home)
    monkeypatch.setattr(reset_cli, "CONFIG_FILE", home / ".codeclaw" / "config.json")
    monkeypatch.setattr("codeclaw.daemon.stop_daemon", lambda: {"running": False})

    with pytest.raises(SystemExit) as exc:
        reset_cli.handle_reset(argparse.Namespace(all=False, config=False, state=False, mcp=True, yes=True))
    assert exc.value.code == 1

    payload = _extract_json(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["mcp"]["error"] is not None
    assert "{ invalid json" == mcp_path.read_text(encoding="utf-8")
