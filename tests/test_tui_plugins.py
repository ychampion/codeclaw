import textwrap

from codeclaw.tui.commands import CommandRegistry
from codeclaw.tui.plugins import PluginManager
from codeclaw.tui.types import CommandResult


def _emit_collector():
    events: list[tuple[str, str]] = []

    def _emit(message: str, level: str = "info") -> None:
        events.append((level, message))

    return events, _emit


def test_plugin_manager_loads_plugin_commands(tmp_path, monkeypatch):
    plugin_root = tmp_path / "plugins" / "echo"
    plugin_root.mkdir(parents=True, exist_ok=True)
    (plugin_root / "plugin.json").write_text(
        '{"name":"echo","version":"0.1.0","entrypoint":"plugin.py"}',
        encoding="utf-8",
    )
    (plugin_root / "plugin.py").write_text(
        textwrap.dedent(
            """
            from codeclaw.tui.types import CommandResult

            def register(ctx):
                def _echo(_app, args):
                    return CommandResult(ok=True, message=" ".join(args) if args else "echo")
                ctx.register_command("echo", _echo, "echo text")
            """
        ),
        encoding="utf-8",
    )

    registry = CommandRegistry()
    events, emit = _emit_collector()
    monkeypatch.setattr("codeclaw.tui.plugins.load_config", lambda: {"disabled_plugins": []})
    monkeypatch.setattr("codeclaw.tui.plugins.save_config", lambda _cfg: None)
    manager = PluginManager(registry=registry, emit=emit, plugin_dirs=[tmp_path / "plugins"])
    records = manager.reload()

    assert any(record.name == "echo" and record.loaded for record in records)
    cmd = registry.get("echo")
    assert cmd is not None
    result = cmd.handler(None, ["hi"])
    assert isinstance(result, CommandResult)
    assert result.ok is True
    assert result.message == "hi"
    assert any("Loaded plugin 'echo'" in message for _, message in events)


def test_plugin_manager_failure_is_isolated(tmp_path, monkeypatch):
    plugin_root = tmp_path / "plugins" / "broken"
    plugin_root.mkdir(parents=True, exist_ok=True)
    (plugin_root / "plugin.json").write_text(
        '{"name":"broken","version":"0.1.0","entrypoint":"plugin.py"}',
        encoding="utf-8",
    )
    (plugin_root / "plugin.py").write_text("def register(ctx):\n    raise RuntimeError('boom')\n", encoding="utf-8")

    registry = CommandRegistry()
    events, emit = _emit_collector()
    monkeypatch.setattr("codeclaw.tui.plugins.load_config", lambda: {"disabled_plugins": []})
    monkeypatch.setattr("codeclaw.tui.plugins.save_config", lambda _cfg: None)
    manager = PluginManager(registry=registry, emit=emit, plugin_dirs=[tmp_path / "plugins"])
    records = manager.reload()

    broken = [record for record in records if record.name == "broken"]
    assert len(broken) == 1
    assert broken[0].loaded is False
    assert broken[0].error is not None
    assert registry.get("broken") is None
    assert any(level == "error" for level, _ in events)


def test_plugin_manager_enable_disable_uses_case_insensitive_names(tmp_path, monkeypatch):
    plugin_root = tmp_path / "plugins" / "echo"
    plugin_root.mkdir(parents=True, exist_ok=True)
    (plugin_root / "plugin.json").write_text(
        '{"name":"Echo","version":"0.1.0","entrypoint":"plugin.py"}',
        encoding="utf-8",
    )
    (plugin_root / "plugin.py").write_text(
        textwrap.dedent(
            """
            from codeclaw.tui.types import CommandResult

            def register(ctx):
                def _echo(_app, args):
                    return CommandResult(ok=True, message="ok")
                ctx.register_command("echo", _echo, "echo text")
            """
        ),
        encoding="utf-8",
    )

    # Keep a mutable in-memory config so plugin state is persisted across calls.
    cfg = {"disabled_plugins": []}

    def _load():
        return dict(cfg)

    def _save(new_cfg):
        cfg.clear()
        cfg.update(new_cfg)

    monkeypatch.setattr("codeclaw.tui.plugins.load_config", _load)
    monkeypatch.setattr("codeclaw.tui.plugins.save_config", _save)

    registry = CommandRegistry()
    _events, emit = _emit_collector()
    manager = PluginManager(registry=registry, emit=emit, plugin_dirs=[tmp_path / "plugins"])

    records = manager.reload()
    assert any(record.name == "Echo" and record.loaded for record in records)
    assert registry.get("echo") is not None

    assert manager.disable("echo") is True
    assert "echo" in cfg["disabled_plugins"]
    assert registry.get("echo") is None

    assert manager.enable("ECHO") is True
    assert "echo" not in cfg["disabled_plugins"]
    assert registry.get("echo") is not None
