import json
import subprocess
import sys
from pathlib import Path

import pytest

from codeclaw import cli as codeclaw_cli
from codeclaw import mcp_server


class _DummyFastMCP:
    def __init__(self, name: str):
        self.name = name
        self.tools: dict[str, object] = {}

    def tool(self):
        def _register(func):
            self.tools[func.__name__] = func
            return func

        return _register


class _DummyGraphIndex:
    def __init__(self, sessions):
        self._sessions = sessions

    def query(self, _nodes, max_results=5):
        return self._sessions[:max_results]

    def stats(self):
        return {"nodes": 3, "edges": 2, "sessions": len(self._sessions), "networkx_available": False}


class _DummySessionService:
    def __init__(self, sessions):
        self._sessions = sessions
        self._refresh_count = 0
        self._index = _DummyGraphIndex(sessions)

    def refresh(self):
        self._refresh_count += 1
        return self.meta()

    def sessions(self):
        return self._sessions

    def index(self):
        return self._index

    def meta(self):
        return {
            "session_count": len(self._sessions),
            "project_count": 1,
            "refresh_count": self._refresh_count,
            "last_refresh_ms": 1.23,
            "index_stats": self._index.stats(),
        }


def _sample_sessions():
    return [
        {
            "session_id": "s-1",
            "project": "proj-a",
            "trajectory_type": "debugging_trace",
            "model": "claude-sonnet",
            "start_time": "2026-02-01T00:00:00+00:00",
            "messages": [
                {"role": "user", "content": "Fix login middleware"},
                {"role": "assistant", "content": "Investigating", "tool_uses": [{"tool": "Read"}]},
            ],
        },
        {
            "session_id": "s-2",
            "project": "proj-b",
            "trajectory_type": "iterative_build",
            "model": "claude-sonnet",
            "start_time": "2026-02-02T00:00:00+00:00",
            "messages": [
                {"role": "user", "content": "Implement dashboard"},
                {"role": "assistant", "content": "Done", "tool_uses": [{"tool": "Write"}]},
            ],
        },
    ]


def _build_dummy_server(monkeypatch, service):
    monkeypatch.setattr(mcp_server, "_get_mcp_or_exit", lambda: _DummyFastMCP)
    return mcp_server.create_mcp_server(
        session_service=service,
        classify_fn=lambda session: str(session.get("trajectory_type", "unknown")),
    )


def test_codeclaw_cli_serve_dispatch(monkeypatch):
    called = {"serve": False}

    def _fake_serve():
        called["serve"] = True

    monkeypatch.setattr(mcp_server, "serve", _fake_serve)
    monkeypatch.setattr(sys, "argv", ["codeclaw", "serve"])

    codeclaw_cli.main()

    assert called["serve"] is True


def test_codeclaw_cli_install_mcp_dispatch(monkeypatch):
    called = {"install": False}

    def _fake_install():
        called["install"] = True

    monkeypatch.setattr(mcp_server, "install_mcp", _fake_install)
    monkeypatch.setattr(sys, "argv", ["codeclaw", "install-mcp"])

    codeclaw_cli.main()

    assert called["install"] is True


def test_install_mcp_writes_claude_config(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    mcp_server.install_mcp()

    config_path = tmp_path / ".claude" / "mcp.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))

    assert "codeclaw" in data["mcpServers"]
    assert data["mcpServers"]["codeclaw"]["args"] == ["-m", "codeclaw.mcp_server", "--serve"]


def test_install_mcp_preserves_other_servers(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    config_path = tmp_path / ".claude" / "mcp.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "existing": {"command": "python", "args": ["-m", "existing.server"]}
                }
            }
        ),
        encoding="utf-8",
    )

    mcp_server.install_mcp()
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert "existing" in data["mcpServers"]
    assert "codeclaw" in data["mcpServers"]


def test_install_mcp_backs_up_corrupt_json(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    config_path = tmp_path / ".claude" / "mcp.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("{not-valid-json", encoding="utf-8")

    mcp_server.install_mcp()

    backup_path = tmp_path / ".claude" / "mcp.json.corrupt.bak"
    assert backup_path.exists()
    assert "{not-valid-json" in backup_path.read_text(encoding="utf-8")

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert "codeclaw" in data["mcpServers"]


def test_python_module_entrypoint_help_smoke():
    proc = subprocess.run(
        [sys.executable, "-m", "codeclaw", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "CodeClaw" in proc.stdout


def test_parse_context_nodes_deduplicates_and_flags_invalid():
    nodes, invalid = mcp_server._parse_context_nodes(
        "tool:bash, file:src/app.py, tool:bash, badnode, error:traceback"
    )
    assert nodes == ["tool:bash", "file:src/app.py", "error:traceback"]
    assert invalid == ["badnode"]


def test_mcp_search_payload_shape(monkeypatch):
    server = _build_dummy_server(monkeypatch, _DummySessionService(_sample_sessions()))
    payload = json.loads(server.tools["search_past_solutions"]("login", max_results=5))

    assert payload["ok"] is True
    assert isinstance(payload["results"], list)
    assert "meta" in payload
    assert payload["results"][0]["rank"] == 1


def test_mcp_find_similar_sessions_invalid_context(monkeypatch):
    server = _build_dummy_server(monkeypatch, _DummySessionService(_sample_sessions()))
    payload = json.loads(server.tools["find_similar_sessions"]("not_a_node", max_results=3))

    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_context"
    assert "tool:" in payload["error"]["message"]


def test_mcp_refresh_index_returns_meta(monkeypatch):
    service = _DummySessionService(_sample_sessions())
    server = _build_dummy_server(monkeypatch, service)
    payload = json.loads(server.tools["refresh_index"]())

    assert payload["ok"] is True
    assert payload["meta"]["refresh_count"] == 1
    assert payload["meta"]["session_count"] == 2


def test_install_mcp_handles_read_oserror(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    config_path = tmp_path / ".claude" / "mcp.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("{}", encoding="utf-8")

    original_read_text = Path.read_text

    def mock_read_text(self, *args, **kwargs):
        if self == config_path:
            raise OSError("Fake OS Error")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", mock_read_text)

    with pytest.raises(SystemExit) as excinfo:
        mcp_server.install_mcp()

    assert excinfo.value.code == 1
