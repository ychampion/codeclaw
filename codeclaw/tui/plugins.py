"""Plugin discovery and lifecycle management for the CodeClaw TUI."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Callable

from ..config import load_config, save_config
from .commands import CommandRegistry
from .types import SlashCommand


@dataclass
class PluginRecord:
    """Status of one discovered plugin."""

    name: str
    version: str
    path: Path
    enabled: bool
    loaded: bool
    entrypoint: str
    description: str = ""
    error: str | None = None
    registered_commands: list[str] = field(default_factory=list)


class PluginContext:
    """Context object passed to each plugin's register hook."""

    def __init__(self, manager: "PluginManager", plugin_name: str) -> None:
        self._manager = manager
        self._plugin_name = plugin_name

    def register_command(
        self,
        name: str,
        handler: Callable,
        help_text: str,
        aliases: tuple[str, ...] = (),
        usage: str | None = None,
    ) -> None:
        command = SlashCommand(
            name=name,
            handler=handler,
            help_text=help_text,
            aliases=aliases,
            usage=usage,
            source=f"plugin:{self._plugin_name}",
        )
        self._manager.registry.register(command)
        self._manager._register_plugin_command(self._plugin_name, command.name)

    def emit(self, message: str, level: str = "info") -> None:
        self._manager.emit(f"[plugin:{self._plugin_name}] {message}", level=level)


class PluginManager:
    """Load/reload/enable/disable plugins without crashing the TUI."""

    def __init__(
        self,
        registry: CommandRegistry,
        emit: Callable[[str, str], None],
        plugin_dirs: list[Path] | None = None,
    ) -> None:
        self.registry = registry
        self.emit = emit
        self.plugin_dirs = plugin_dirs or []
        self.records: dict[str, PluginRecord] = {}
        self._modules: dict[str, ModuleType] = {}

    @staticmethod
    def _normalize_plugin_name(name: str) -> str:
        return name.strip().lower()

    @staticmethod
    def default_plugin_dirs() -> list[Path]:
        return [
            Path.cwd() / "plugins",
            Path.home() / ".codeclaw" / "plugins",
        ]

    def _disabled_plugins(self) -> set[str]:
        cfg = load_config()
        return {
            self._normalize_plugin_name(str(name))
            for name in cfg.get("disabled_plugins", [])
            if str(name).strip()
        }

    def _set_plugin_enabled(self, name: str, enabled: bool) -> None:
        cfg = load_config()
        disabled = {
            self._normalize_plugin_name(str(item))
            for item in cfg.get("disabled_plugins", [])
            if str(item).strip()
        }
        normalized = self._normalize_plugin_name(name)
        if enabled:
            disabled.discard(normalized)
        else:
            disabled.add(normalized)
        cfg["disabled_plugins"] = sorted(disabled)
        save_config(cfg)

    def _find_record(self, name: str) -> PluginRecord | None:
        needle = self._normalize_plugin_name(name)
        for record in self.records.values():
            if self._normalize_plugin_name(record.name) == needle:
                return record
        return None

    def enable(self, name: str) -> bool:
        self._set_plugin_enabled(name, True)
        self.reload()
        record = self._find_record(name)
        return bool(record and record.enabled and record.loaded and not record.error)

    def disable(self, name: str) -> bool:
        self._set_plugin_enabled(name, False)
        self.reload()
        record = self._find_record(name)
        return bool(record and not record.enabled)

    def _iter_plugin_manifests(self) -> list[Path]:
        manifests: list[Path] = []
        for root in self.plugin_dirs:
            if not root.exists() or not root.is_dir():
                continue
            for child in sorted(root.iterdir()):
                if not child.is_dir():
                    continue
                manifest = child / "plugin.json"
                if manifest.exists():
                    manifests.append(manifest)
        return manifests

    def _load_manifest(self, path: Path) -> dict[str, str]:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("plugin.json must be a JSON object.")
        name = str(parsed.get("name", "")).strip()
        version = str(parsed.get("version", "")).strip()
        if not name:
            raise ValueError("plugin.json missing required field: name")
        if not version:
            raise ValueError("plugin.json missing required field: version")
        entrypoint = str(parsed.get("entrypoint", "plugin.py")).strip() or "plugin.py"
        description = str(parsed.get("description", "")).strip()
        return {
            "name": name,
            "version": version,
            "entrypoint": entrypoint,
            "description": description,
        }

    def _load_module(self, plugin_name: str, entrypoint: Path) -> ModuleType:
        module_name = f"codeclaw_tui_plugin_{plugin_name.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, entrypoint)
        if spec is None or spec.loader is None:
            raise RuntimeError("Failed to build import spec for plugin entrypoint.")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _register_plugin_command(self, plugin_name: str, command_name: str) -> None:
        record = self.records.get(plugin_name)
        if record is None:
            return
        if command_name not in record.registered_commands:
            record.registered_commands.append(command_name)

    def reload(self) -> list[PluginRecord]:
        for name in list(self.records):
            self.registry.unregister_by_source(f"plugin:{name}")
        self.records = {}
        self._modules = {}

        disabled = self._disabled_plugins()
        for manifest in self._iter_plugin_manifests():
            try:
                data = self._load_manifest(manifest)
                plugin_name = data["name"]
                plugin_key = self._normalize_plugin_name(plugin_name)
                if any(self._normalize_plugin_name(existing) == plugin_key for existing in self.records):
                    raise ValueError(f"Duplicate plugin name detected: {plugin_name}")
                plugin_root = manifest.parent
                entrypoint = plugin_root / data["entrypoint"]
                enabled = plugin_key not in disabled
                record = PluginRecord(
                    name=plugin_name,
                    version=data["version"],
                    path=plugin_root,
                    enabled=enabled,
                    loaded=False,
                    entrypoint=str(entrypoint),
                    description=data["description"],
                )
                self.records[plugin_name] = record
                if not enabled:
                    continue
                if not entrypoint.exists():
                    raise FileNotFoundError(f"Plugin entrypoint not found: {entrypoint}")
                module = self._load_module(plugin_name, entrypoint)
                register_fn = getattr(module, "register", None)
                if not callable(register_fn):
                    raise AttributeError("Plugin entrypoint must define register(ctx).")
                ctx = PluginContext(self, plugin_name=plugin_name)
                register_fn(ctx)
                record.loaded = True
                self._modules[plugin_name] = module
                self.emit(
                    f"Loaded plugin '{plugin_name}' (commands: {', '.join(record.registered_commands) or 'none'})",
                    level="info",
                )
            except Exception as exc:
                fallback_name = manifest.parent.name
                existing = self.records.get(fallback_name)
                if existing is None:
                    self.records[fallback_name] = PluginRecord(
                        name=fallback_name,
                        version="unknown",
                        path=manifest.parent,
                        enabled=True,
                        loaded=False,
                        entrypoint=str(manifest.parent / "plugin.py"),
                        error=f"{type(exc).__name__}: {exc}",
                    )
                else:
                    existing.error = f"{type(exc).__name__}: {exc}"
                self.emit(
                    f"Plugin load failed ({manifest.parent.name}): {type(exc).__name__}: {exc}",
                    level="error",
                )

        return self.list_records()

    def list_records(self) -> list[PluginRecord]:
        return [self.records[name] for name in sorted(self.records)]
