from __future__ import annotations

from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput

from codeclaw.tui.app import CodeClawTuiApp


def _build_app(monkeypatch, tmp_path, source: str = "auto"):
    cfg: dict = {
        "connected_projects": [],
        "projects_confirmed": False,
        "disabled_plugins": [],
    }

    def _load():
        return dict(cfg)

    def _save(new_cfg):
        cfg.clear()
        cfg.update(new_cfg)

    monkeypatch.setattr("codeclaw.tui.app.Path.home", lambda: tmp_path)
    monkeypatch.setattr("codeclaw.tui.plugins.Path.home", lambda: tmp_path)
    monkeypatch.setattr("codeclaw.tui.app.load_config", _load)
    monkeypatch.setattr("codeclaw.tui.app.save_config", _save)
    monkeypatch.setattr("codeclaw.tui.plugins.load_config", _load)
    monkeypatch.setattr("codeclaw.tui.plugins.save_config", _save)
    app = CodeClawTuiApp(
        source=source,
        plugin_dirs=[],
        pt_output=DummyOutput(),
        pt_input=DummyInput(),
    )
    return app, cfg


def test_tui_logs_command_shows_recent_logs(monkeypatch, tmp_path):
    app, _cfg = _build_app(monkeypatch, tmp_path, source="auto")
    monkeypatch.setattr("codeclaw.tui.app.read_recent_logs", lambda lines=40: ["line-a", "line-b"])

    result = app._cmd_logs(None, ["2"])

    assert result.ok is True
    assert "logs shown" in result.message
    assert any("line-a" in line for line in app.feed_lines)
    app.jobs.shutdown()


def test_tui_scope_command_updates_connected_projects(monkeypatch, tmp_path):
    app, cfg = _build_app(monkeypatch, tmp_path, source="codex")
    monkeypatch.setattr(
        "codeclaw.tui.app.discover_projects",
        lambda: [
            {"display_name": "codex:alpha", "source": "codex"},
            {"display_name": "claude:beta", "source": "claude"},
        ],
    )

    by_index = app._cmd_scope(None, ["1"])
    assert by_index.ok is True
    assert cfg["connected_projects"] == ["codex:alpha"]
    assert cfg["source"] == "codex"

    clear = app._cmd_scope(None, ["all"])
    assert clear.ok is True
    assert cfg["connected_projects"] == []

    invalid = app._cmd_scope(None, ["missing-project"])
    assert invalid.ok is False
    app.jobs.shutdown()


def test_tui_source_command_updates_config(monkeypatch, tmp_path):
    app, cfg = _build_app(monkeypatch, tmp_path, source="auto")

    show = app._cmd_source(None, [])
    assert show.ok is True
    assert "current source: auto" in show.message

    set_codex = app._cmd_source(None, ["codex"])
    assert set_codex.ok is True
    assert cfg["source"] == "codex"

    set_auto = app._cmd_source(None, ["auto"])
    assert set_auto.ok is True
    assert cfg["source"] is None

    bad = app._cmd_source(None, ["invalid-source"])
    assert bad.ok is False
    app.jobs.shutdown()
