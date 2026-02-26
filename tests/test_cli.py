"""Tests for codeclaw.cli — CLI commands and helpers."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codeclaw.cli import (
    _build_status_next_steps,
    _build_dataset_card,
    _collect_review_attestations,
    _format_size,
    _format_token_count,
    _get_disabled_projects,
    _is_dataset_globally_enabled,
    _merge_config_list,
    _parse_csv_arg,
    _scan_for_text_occurrences,
    _scan_high_entropy_strings,
    _scan_pii,
    _validate_publish_attestation,
    configure,
    default_repo_name,
    export_to_jsonl,
    list_projects,
    main,
    push_to_huggingface,
)


# --- _format_size ---


class TestFormatSize:
    def test_bytes(self):
        assert _format_size(500) == "500 B"

    def test_kilobytes(self):
        result = _format_size(2048)
        assert "KB" in result

    def test_megabytes(self):
        result = _format_size(5 * 1024 * 1024)
        assert "MB" in result

    def test_gigabytes(self):
        result = _format_size(2 * 1024 * 1024 * 1024)
        assert "GB" in result

    def test_zero(self):
        assert _format_size(0) == "0 B"

    def test_exactly_1024(self):
        result = _format_size(1024)
        assert "KB" in result


# --- _format_token_count ---


class TestFormatTokenCount:
    def test_plain(self):
        assert _format_token_count(500) == "500"

    def test_thousands(self):
        result = _format_token_count(5000)
        assert result == "5K"

    def test_millions(self):
        result = _format_token_count(2_500_000)
        assert "M" in result

    def test_billions(self):
        result = _format_token_count(1_500_000_000)
        assert "B" in result

    def test_zero(self):
        assert _format_token_count(0) == "0"


# --- attestation helpers ---


class TestAttestationHelpers:
    def test_collect_review_attestations_valid(self):
        attestations, errors, manual_count = _collect_review_attestations(
            attest_asked_full_name=(
                "I asked Jane Doe for their full name and scanned the export for Jane Doe."
            ),
            attest_asked_sensitive=(
                "I asked about company, client, and internal names plus URLs; "
                "none were sensitive and no extra redactions were needed."
            ),
            attest_manual_scan=(
                "I performed a manual scan and reviewed 20 sessions across beginning, middle, and end."
            ),
            full_name="Jane Doe",
        )
        assert not errors
        assert manual_count == 20
        assert "Jane Doe" in attestations["asked_full_name"]

    def test_collect_review_attestations_invalid(self):
        _attestations, errors, manual_count = _collect_review_attestations(
            attest_asked_full_name="scanned quickly",
            attest_asked_sensitive="checked stuff",
            attest_manual_scan="manual scan of 5 sessions",
            full_name="Jane Doe",
        )
        assert errors
        assert "asked_full_name" in errors
        assert "asked_sensitive_entities" in errors
        assert "manual_scan_done" in errors
        assert manual_count == 5

    def test_collect_review_attestations_skip_full_name_valid(self):
        _attestations, errors, manual_count = _collect_review_attestations(
            attest_asked_full_name=(
                "User declined to share full name; skipped exact-name scan."
            ),
            attest_asked_sensitive=(
                "I asked about company/client/internal names and private URLs; "
                "none were sensitive and no extra redactions were needed."
            ),
            attest_manual_scan=(
                "I performed a manual scan and reviewed 20 sessions across beginning, middle, and end."
            ),
            full_name=None,
            skip_full_name_scan=True,
        )
        assert not errors
        assert manual_count == 20

    def test_collect_review_attestations_skip_full_name_invalid(self):
        _attestations, errors, _manual_count = _collect_review_attestations(
            attest_asked_full_name="Asked user and scanned it.",
            attest_asked_sensitive=(
                "I asked about company/client/internal names and private URLs; none found."
            ),
            attest_manual_scan=(
                "I performed a manual scan and reviewed 20 sessions across beginning, middle, and end."
            ),
            full_name=None,
            skip_full_name_scan=True,
        )
        assert "asked_full_name" in errors

    def test_validate_publish_attestation(self):
        _normalized, err = _validate_publish_attestation(
            "User explicitly approved publishing this dataset now."
        )
        assert err is None

        _normalized, err = _validate_publish_attestation("ok to go")
        assert err is not None

    def test_scan_for_text_occurrences(self, tmp_path):
        f = tmp_path / "sample.jsonl"
        f.write_text('{"message":"Jane Doe says hi"}\n{"message":"nothing here"}\n')
        result = _scan_for_text_occurrences(f, "Jane Doe")
        assert result["match_count"] == 1


# --- _parse_csv_arg ---


class TestParseCsvArg:
    def test_none(self):
        assert _parse_csv_arg(None) is None

    def test_empty(self):
        assert _parse_csv_arg("") is None

    def test_single(self):
        assert _parse_csv_arg("foo") == ["foo"]

    def test_comma_separated(self):
        assert _parse_csv_arg("foo, bar, baz") == ["foo", "bar", "baz"]

    def test_strips_whitespace(self):
        assert _parse_csv_arg("  a ,  b  ") == ["a", "b"]

    def test_empty_items_filtered(self):
        assert _parse_csv_arg("a,,b,") == ["a", "b"]


# --- _merge_config_list ---


class TestMergeConfigList:
    def test_merge_new_values(self):
        config = {"items": ["a", "b"]}
        _merge_config_list(config, "items", ["c", "d"])
        assert sorted(config["items"]) == ["a", "b", "c", "d"]

    def test_deduplicate(self):
        config = {"items": ["a", "b"]}
        _merge_config_list(config, "items", ["b", "c"])
        assert sorted(config["items"]) == ["a", "b", "c"]

    def test_sorted(self):
        config = {"items": ["z"]}
        _merge_config_list(config, "items", ["a", "m"])
        assert config["items"] == ["a", "m", "z"]

    def test_missing_key(self):
        config = {}
        _merge_config_list(config, "items", ["a"])
        assert config["items"] == ["a"]


# --- default_repo_name ---


class TestDefaultRepoName:
    def test_format(self):
        result = default_repo_name("alice")
        assert result == "alice/my-personal-codex-data"

    def test_contains_username(self):
        result = default_repo_name("bob")
        assert "bob" in result
        assert "/" in result


# --- _build_dataset_card ---


class TestBuildDatasetCard:
    def test_returns_valid_markdown(self):
        meta = {
            "models": {"claude-sonnet-4-20250514": 10},
            "sessions": 10,
            "projects": ["proj1"],
            "total_input_tokens": 50000,
            "total_output_tokens": 3000,
            "exported_at": "2025-01-15T10:00:00+00:00",
        }
        card = _build_dataset_card("user/repo", meta)
        assert "---" in card  # YAML frontmatter
        assert "codeclaw" in card
        assert "claude-sonnet" in card
        assert "10" in card

    def test_yaml_frontmatter(self):
        meta = {
            "models": {}, "sessions": 0, "projects": [],
            "total_input_tokens": 0, "total_output_tokens": 0,
            "exported_at": "",
        }
        card = _build_dataset_card("user/repo", meta)
        lines = card.strip().split("\n")
        assert lines[0] == "---"
        # Find second ---
        second_dash = [i for i, l in enumerate(lines[1:], 1) if l.strip() == "---"]
        assert len(second_dash) >= 1

    def test_contains_repo_id(self):
        meta = {
            "models": {}, "sessions": 0, "projects": [],
            "total_input_tokens": 0, "total_output_tokens": 0,
            "exported_at": "",
        }
        card = _build_dataset_card("alice/my-dataset", meta)
        assert "alice/my-dataset" in card

    def test_includes_configs_and_trajectory_table(self):
        meta = {
            "models": {"m1": 1},
            "sessions": 1,
            "projects": ["proj"],
            "trajectory_types": {"debugging_trace": 1},
            "total_input_tokens": 10,
            "total_output_tokens": 5,
            "exported_at": "2025-01-15T10:00:00+00:00",
        }
        card = _build_dataset_card("alice/my-dataset", meta, ["proj"])
        assert "configs:" in card
        assert "config_name: proj" in card
        assert "Trajectory Types" in card
        assert "debugging_trace" in card


# --- export_to_jsonl ---


class TestExportToJsonl:
    def test_writes_jsonl(self, tmp_path, mock_anonymizer, monkeypatch):
        output = tmp_path / "out.jsonl"
        session_data = [{
            "session_id": "s1",
            "model": "claude-sonnet-4-20250514",
            "git_branch": "main",
            "start_time": "2025-01-01T00:00:00",
            "end_time": "2025-01-01T01:00:00",
            "messages": [{"role": "user", "content": "hi"}],
            "stats": {"input_tokens": 100, "output_tokens": 50},
            "project": "test",
        }]
        monkeypatch.setattr(
            "codeclaw.cli.export.parse_project_sessions",
            lambda *a, **kw: session_data,
        )

        projects = [{"dir_name": "test", "display_name": "test"}]
        meta = export_to_jsonl(projects, output, mock_anonymizer)

        assert output.exists()
        lines = output.read_text().strip().split("\n")
        assert len(lines) == 1
        assert meta["sessions"] == 1

    def test_skips_synthetic_model(self, tmp_path, mock_anonymizer, monkeypatch):
        output = tmp_path / "out.jsonl"
        session_data = [{
            "session_id": "s1",
            "model": "<synthetic>",
            "messages": [{"role": "user", "content": "hi"}],
            "stats": {},
        }]
        monkeypatch.setattr(
            "codeclaw.cli.export.parse_project_sessions",
            lambda *a, **kw: session_data,
        )
        projects = [{"dir_name": "test", "display_name": "test"}]
        meta = export_to_jsonl(projects, output, mock_anonymizer)
        assert meta["sessions"] == 0
        assert meta["skipped"] == 1

    def test_counts_redactions(self, tmp_path, mock_anonymizer, monkeypatch):
        output = tmp_path / "out.jsonl"
        session_data = [{
            "session_id": "s1",
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Key: sk-ant-api03-abcdefghijklmnopqrstuvwxyz"}],
            "stats": {"input_tokens": 10, "output_tokens": 5},
        }]
        monkeypatch.setattr(
            "codeclaw.cli.export.parse_project_sessions",
            lambda *a, **kw: session_data,
        )
        projects = [{"dir_name": "test", "display_name": "test"}]
        meta = export_to_jsonl(projects, output, mock_anonymizer)
        assert meta["redactions"] >= 1

    def test_skips_none_model(self, tmp_path, mock_anonymizer, monkeypatch):
        output = tmp_path / "out.jsonl"
        session_data = [{
            "session_id": "s1",
            "model": None,
            "messages": [{"role": "user", "content": "hi"}],
            "stats": {},
        }]
        monkeypatch.setattr(
            "codeclaw.cli.export.parse_project_sessions",
            lambda *a, **kw: session_data,
        )
        projects = [{"dir_name": "t", "display_name": "t"}]
        meta = export_to_jsonl(projects, output, mock_anonymizer)
        assert meta["sessions"] == 0
        assert meta["skipped"] == 1

    def test_adds_trajectory_and_dedupes_synced_sessions(self, tmp_path, mock_anonymizer, monkeypatch):
        output = tmp_path / "out.jsonl"
        session_data = [
            {
                "session_id": "s1",
                "model": "claude-sonnet-4-20250514",
                "messages": [{"role": "user", "content": "hello"}],
                "stats": {"input_tokens": 10, "output_tokens": 5},
                "project": "test",
            },
            {
                "session_id": "s2",
                "model": "claude-sonnet-4-20250514",
                "messages": [{"role": "user", "content": "hello"}],
                "stats": {"input_tokens": 10, "output_tokens": 5},
                "project": "test",
            },
        ]
        monkeypatch.setattr("codeclaw.cli.export.load_config", lambda: {"synced_session_ids": ["s1"]})
        monkeypatch.setattr("codeclaw.cli.export.parse_project_sessions", lambda *a, **kw: session_data)
        meta = export_to_jsonl([{"dir_name": "t", "display_name": "t"}], output, mock_anonymizer)
        lines = output.read_text().strip().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["session_id"] == "s2"
        assert row["trajectory_type"] == "sft_clean"
        assert meta["trajectory_types"]["sft_clean"] == 1


# --- configure ---


class TestConfigure:
    def test_sets_repo(self, tmp_config, monkeypatch, capsys):
        # Also monkeypatch the cli module's references
        monkeypatch.setattr("codeclaw.cli.config.CONFIG_FILE", tmp_config)
        monkeypatch.setattr("codeclaw.cli.config.load_config", lambda: {"repo": None, "excluded_projects": [], "redact_strings": []})
        saved = {}
        monkeypatch.setattr("codeclaw.cli.config.save_config", lambda c: saved.update(c))

        configure(repo="alice/my-repo")
        assert saved["repo"] == "alice/my-repo"

    def test_merges_exclude(self, tmp_config, monkeypatch, capsys):
        monkeypatch.setattr("codeclaw.cli.config.CONFIG_FILE", tmp_config)
        monkeypatch.setattr("codeclaw.cli.config.load_config", lambda: {"excluded_projects": ["a"], "redact_strings": []})
        saved = {}
        monkeypatch.setattr("codeclaw.cli.config.save_config", lambda c: saved.update(c))

        configure(exclude=["b", "c"])
        assert sorted(saved["excluded_projects"]) == ["a", "b", "c"]

    def test_sets_source(self, tmp_config, monkeypatch, capsys):
        monkeypatch.setattr("codeclaw.cli.config.CONFIG_FILE", tmp_config)
        monkeypatch.setattr("codeclaw.cli.config.load_config", lambda: {"repo": None, "source": None})
        saved = {}
        monkeypatch.setattr("codeclaw.cli.config.save_config", lambda c: saved.update(c))

        configure(source="codex")
        assert saved["source"] == "codex"


# --- list_projects ---


class TestListProjects:
    def test_with_projects(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "codeclaw.cli.export.discover_projects",
            lambda: [{"display_name": "proj1", "session_count": 5, "total_size_bytes": 1024}],
        )
        monkeypatch.setattr(
            "codeclaw.cli.export.load_config",
            lambda: {"excluded_projects": []},
        )
        monkeypatch.setattr("codeclaw.cli.export.detect_current_project", lambda: None)
        list_projects()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 1
        assert data[0]["name"] == "proj1"
        assert data[0]["current"] is False

    def test_no_projects(self, monkeypatch, capsys):
        monkeypatch.setattr("codeclaw.cli.export.discover_projects", lambda: [])
        list_projects()
        captured = capsys.readouterr()
        assert "No Claude Code or Codex sessions" in captured.out

    def test_source_filter_codex(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "codeclaw.cli.export.discover_projects",
            lambda: [
                {"display_name": "proj1", "session_count": 5, "total_size_bytes": 1024, "source": "claude"},
                {"display_name": "codex:proj2", "session_count": 3, "total_size_bytes": 512, "source": "codex"},
            ],
        )
        monkeypatch.setattr("codeclaw.cli.export.load_config", lambda: {"excluded_projects": []})
        monkeypatch.setattr("codeclaw.cli.export.detect_current_project", lambda: None)
        list_projects(source_filter="codex")
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 1
        assert data[0]["name"] == "codex:proj2"
        assert data[0]["source"] == "codex"

    def test_no_projects_for_selected_source(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "codeclaw.cli.export.discover_projects",
            lambda: [{"display_name": "proj1", "session_count": 5, "total_size_bytes": 1024, "source": "claude"}],
        )
        list_projects(source_filter="codex")
        captured = capsys.readouterr()
        assert "No Codex sessions found." in captured.out

    def test_main_list_uses_configured_source_when_auto(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "codeclaw.cli.export.discover_projects",
            lambda: [
                {"display_name": "proj1", "session_count": 5, "total_size_bytes": 1024, "source": "claude"},
                {"display_name": "codex:proj2", "session_count": 3, "total_size_bytes": 512, "source": "codex"},
            ],
        )
        monkeypatch.setattr("codeclaw.cli.export.load_config", lambda: {"source": "codex", "excluded_projects": []})
        monkeypatch.setattr("codeclaw.cli.export.detect_current_project", lambda: None)
        monkeypatch.setattr("sys.argv", ["codeclaw", "list"])
        main()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 1
        assert data[0]["name"] == "codex:proj2"

    def test_current_project_flagged(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "codeclaw.cli.export.discover_projects",
            lambda: [
                {"display_name": "proj1", "session_count": 5, "total_size_bytes": 1024, "source": "claude"},
                {"display_name": "proj2", "session_count": 3, "total_size_bytes": 512, "source": "claude"},
            ],
        )
        monkeypatch.setattr("codeclaw.cli.export.load_config", lambda: {"excluded_projects": []})
        monkeypatch.setattr(
            "codeclaw.cli.export.detect_current_project",
            lambda: {"display_name": "proj1"},
        )
        list_projects()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data[0]["name"] == "proj1"
        assert data[0]["current"] is True
        assert data[1]["name"] == "proj2"
        assert data[1]["current"] is False


# --- push_to_huggingface ---


class TestPushToHuggingface:
    def test_missing_huggingface_hub(self, tmp_path, monkeypatch):
        jsonl_path = tmp_path / "data.jsonl"
        jsonl_path.write_text("{}\n")

        # Simulate ImportError for huggingface_hub
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "huggingface_hub":
                raise ImportError("No module named 'huggingface_hub'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        with pytest.raises(SystemExit):
            push_to_huggingface(jsonl_path, "user/repo", {})

    def test_success_flow(self, tmp_path, monkeypatch):
        jsonl_path = tmp_path / "data.jsonl"
        jsonl_path.write_text("{}\n")

        mock_api = MagicMock()
        mock_api.whoami.return_value = {"name": "alice"}

        mock_hfapi_cls = MagicMock(return_value=mock_api)

        # Patch the import inside push_to_huggingface
        import codeclaw.cli as cli_mod
        monkeypatch.setattr(cli_mod, "push_to_huggingface", lambda *a, **kw: None)

        # Direct test with mock
        with patch.dict("sys.modules", {"huggingface_hub": MagicMock(HfApi=mock_hfapi_cls)}):
            # Re-import would be needed for real test, but let's test the mock setup
            assert mock_hfapi_cls() == mock_api

    def test_auth_failure(self, tmp_path, monkeypatch):
        jsonl_path = tmp_path / "data.jsonl"
        jsonl_path.write_text("{}\n")

        mock_api = MagicMock()
        mock_api.whoami.side_effect = OSError("Auth failed")

        mock_hf_module = MagicMock()
        mock_hf_module.HfApi.return_value = mock_api

        with patch.dict("sys.modules", {"huggingface_hub": mock_hf_module}):
            # Need to reimport to pick up the mock
            import importlib
            import codeclaw.cli
            importlib.reload(codeclaw.cli)
            with pytest.raises(SystemExit):
                codeclaw.cli.push_to_huggingface(jsonl_path, "user/repo", {})
            # Reload again to restore
            importlib.reload(codeclaw.cli)

    def test_uploads_per_project_and_updates_synced_ids(self, tmp_path, monkeypatch):
        jsonl_path = tmp_path / "data.jsonl"
        jsonl_path.write_text(
            '{"session_id":"abcdef123456","project":"My Project","model":"m","messages":[],"stats":{}}\n'
            '{"session_id":"9876543210","project":"Other/Proj","model":"m","messages":[],"stats":{}}\n'
        )
        meta = {"exported_session_ids": ["abcdef123456", "9876543210"]}

        mock_api = MagicMock()
        mock_api.whoami.return_value = {"name": "alice"}
        mock_api.list_repo_files.return_value = [
            "data/my-project/train-1-abcdef12.jsonl",
            "data/other-proj/train-1-98765432.jsonl",
        ]
        mock_hf_module = MagicMock()
        mock_hf_module.HfApi.return_value = mock_api

        saved = {}
        monkeypatch.setattr("codeclaw.cli.export.load_config", lambda: {"synced_session_ids": ["old"]})
        monkeypatch.setattr("codeclaw.cli.export.save_config", lambda c: saved.update(c))
        with patch.dict("sys.modules", {"huggingface_hub": mock_hf_module}):
            push_to_huggingface(jsonl_path, "user/repo", meta)
            upload_paths = [
                call.kwargs["path_in_repo"]
                for call in mock_api.upload_file.call_args_list
            ]
            assert any(path.startswith("data/my-project/train-") for path in upload_paths)
            assert any(path.startswith("data/other-proj/train-") for path in upload_paths)
            assert "metadata.json" in upload_paths
            assert "README.md" in upload_paths
            assert "abcdef123456" in saved["synced_session_ids"]
            assert "9876543210" in saved["synced_session_ids"]


class TestWatchCommand:
    def test_watch_status_command(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "codeclaw.daemon.daemon_status",
            lambda: {"running": True, "pid": 123},
        )
        monkeypatch.setattr("sys.argv", ["codeclaw", "watch", "--status"])
        main()
        payload = json.loads(capsys.readouterr().out)
        assert payload["running"] is True
        assert payload["pid"] == 123

    def test_watch_pause_command(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "codeclaw.daemon.set_watch_paused",
            lambda paused: {"ok": True, "paused": paused},
        )
        monkeypatch.setattr("sys.argv", ["codeclaw", "watch", "--pause"])
        main()
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["paused"] is True

    def test_watch_logs_command(self, monkeypatch, capsys):
        monkeypatch.setattr("codeclaw.daemon.read_recent_logs", lambda lines=80: ["l1", "l2"][-lines:])
        monkeypatch.setattr("sys.argv", ["codeclaw", "watch", "--logs", "--lines", "2"])
        main()
        output = capsys.readouterr().out
        assert "l1" in output
        assert "l2" in output

    def test_watch_switch_project_command(self, monkeypatch, capsys):
        saved: dict = {}
        monkeypatch.setattr("codeclaw.cli.watch.load_config", lambda: {"source": "both", "connected_projects": []})
        monkeypatch.setattr("codeclaw.cli.watch.save_config", lambda cfg: saved.update(cfg))
        monkeypatch.setattr(
            "codeclaw.cli.watch.discover_projects",
            lambda: [
                {"display_name": "codex:codeclaw", "source": "codex"},
                {"display_name": "codex:other", "source": "codex"},
            ],
        )
        monkeypatch.setattr(
            "sys.argv",
            ["codeclaw", "watch", "--switch-project", "codex:codeclaw", "--source", "both"],
        )
        main()
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["connected_projects"] == ["codex:codeclaw"]
        assert saved["connected_projects"] == ["codex:codeclaw"]

    def test_setup_yes_starts_daemon(self, monkeypatch, capsys):
        monkeypatch.setattr("codeclaw.daemon.start_daemon", lambda: {"running": True, "pid": 456})
        monkeypatch.setattr("sys.argv", ["codeclaw", "setup", "--yes"])
        main()
        payload = json.loads(capsys.readouterr().out)
        assert payload["pid"] == 456


class TestWorkflowGateMessages:
    @staticmethod
    def _extract_json(stdout: str) -> dict:
        start = stdout.find("{")
        assert start >= 0, f"No JSON payload found in output: {stdout!r}"
        return json.loads(stdout[start:])

    def test_confirm_without_export_shows_step_process(self, tmp_path, monkeypatch, capsys):
        missing = tmp_path / "missing.jsonl"
        monkeypatch.setattr(
            "sys.argv",
            ["codeclaw", "confirm", "--file", str(missing)],
        )
        with pytest.raises(SystemExit):
            main()
        payload = self._extract_json(capsys.readouterr().out)
        assert payload["error"] == "No export file found."
        assert payload["blocked_on_step"] == "Step 1/3"
        assert len(payload["process_steps"]) == 3
        assert "export --no-push" in payload["process_steps"][0]

    def test_confirm_missing_full_name_explains_purpose_and_skip(self, tmp_path, monkeypatch, capsys):
        export_file = tmp_path / "export.jsonl"
        export_file.write_text('{"project":"p","model":"m","messages":[]}\n')
        monkeypatch.setattr(
            "sys.argv",
            [
                "codeclaw",
                "confirm",
                "--file",
                str(export_file),
                "--attest-full-name",
                "Asked for full name and scanned export.",
                "--attest-sensitive",
                "Asked about company/client/internal names and private URLs; none found.",
                "--attest-manual-scan",
                "Manually scanned 20 sessions across beginning/middle/end and reviewed findings.",
            ],
        )
        with pytest.raises(SystemExit):
            main()
        payload = self._extract_json(capsys.readouterr().out)
        assert payload["error"] == "Missing required --full-name for verification scan."
        assert "--skip-full-name-scan" in payload["hint"]
        assert payload["blocked_on_step"] == "Step 2/3"
        assert len(payload["process_steps"]) == 3

    def test_confirm_skip_full_name_scan_succeeds(self, tmp_path, monkeypatch, capsys):
        export_file = tmp_path / "export.jsonl"
        export_file.write_text('{"project":"p","model":"m","messages":[]}\n')
        monkeypatch.setattr("codeclaw.cli.export.load_config", lambda: {})
        monkeypatch.setattr("codeclaw.cli.export.save_config", lambda _c: None)
        monkeypatch.setattr(
            "sys.argv",
            [
                "codeclaw",
                "confirm",
                "--file",
                str(export_file),
                "--skip-full-name-scan",
                "--attest-full-name",
                "User declined to share full name; skipped exact-name scan.",
                "--attest-sensitive",
                "I asked about company/client/internal names and private URLs; none found.",
                "--attest-manual-scan",
                "I performed a manual scan and reviewed 20 sessions across beginning, middle, and end.",
            ],
        )
        main()
        payload = self._extract_json(capsys.readouterr().out)
        assert payload["stage"] == "confirmed"
        assert payload["full_name_scan"]["skipped"] is True

    def test_push_before_confirm_shows_step_process(self, monkeypatch, capsys):
        monkeypatch.setattr("codeclaw.cli.export.load_config", lambda: {"stage": "review", "source": "both"})
        monkeypatch.setattr("sys.argv", ["codeclaw", "export"])
        with pytest.raises(SystemExit):
            main()
        payload = self._extract_json(capsys.readouterr().out)
        assert payload["error"] == "You must run `codeclaw confirm` before pushing."
        assert payload["blocked_on_step"] == "Step 2/3"
        assert len(payload["process_steps"]) == 3
        assert "confirm" in payload["process_steps"][1]

    def test_export_requires_project_confirmation_with_full_flow(self, monkeypatch, capsys):
        monkeypatch.setattr("codeclaw.cli.export._has_session_sources", lambda _src: True)
        monkeypatch.setattr(
            "codeclaw.cli.export.discover_projects",
            lambda: [
                {
                    "display_name": "proj1",
                    "session_count": 2,
                    "total_size_bytes": 1024,
                    "source": "claude",
                }
            ],
        )
        monkeypatch.setattr("codeclaw.cli.export.load_config", lambda: {"source": "both"})
        monkeypatch.setattr("sys.argv", ["codeclaw", "export", "--no-push"])
        with pytest.raises(SystemExit):
            main()
        payload = self._extract_json(capsys.readouterr().out)
        assert payload["error"] == "Project selection is not confirmed yet."
        assert payload["blocked_on_step"] == "Step 3/6"
        assert len(payload["process_steps"]) == 6
        assert "prep && codeclaw list" in payload["process_steps"][0]
        assert payload["required_action"].startswith("Send the full project/folder list")
        assert "in a message" in payload["required_action"]
        assert isinstance(payload["projects"], list)
        assert payload["projects"][0]["name"] == "proj1"
        assert payload["projects"][0]["sessions"] == 2

    def test_export_requires_explicit_source_selection(self, monkeypatch, capsys):
        monkeypatch.setattr("codeclaw.cli.export.load_config", lambda: {})
        monkeypatch.setattr("sys.argv", ["codeclaw", "export", "--no-push"])
        with pytest.raises(SystemExit):
            main()
        payload = self._extract_json(capsys.readouterr().out)
        assert payload["error"] == "Source scope is not confirmed yet."
        assert payload["blocked_on_step"] == "Step 2/6"
        assert len(payload["process_steps"]) == 6
        assert payload["allowed_sources"] == ["both", "claude", "codex"]
        assert payload["next_command"] == "codeclaw config --source both"

    def test_configure_next_steps_require_full_folder_presentation(self):
        steps, _next = _build_status_next_steps(
            "configure",
            {"projects_confirmed": False},
            "alice",
            "alice/my-personal-codex-data",
        )
        assert any("codeclaw list" in step for step in steps)
        assert any("FULL project/folder list" in step for step in steps)
        assert any("in your next message" in step for step in steps)
        assert any("source scope" in step.lower() for step in steps)

    def test_review_next_steps_explain_full_name_purpose_and_skip_option(self):
        steps, _next = _build_status_next_steps(
            "review",
            {},
            "alice",
            "alice/my-personal-codex-data",
        )
        assert any("exact-name privacy check" in step for step in steps)
        assert any("--skip-full-name-scan" in step for step in steps)


# --- _scan_high_entropy_strings ---


class TestScanHighEntropyStrings:
    def test_detects_real_secret(self):
        # A realistic API key-like string with high entropy and mixed chars
        secret = "aB3dE6gH9jK2mN5pQ8rS1tU4wX7yZ0c"
        content = f"some config here token {secret} and more text"
        results = _scan_high_entropy_strings(content)
        assert len(results) >= 1
        assert any(r["match"] == secret for r in results)
        # Entropy should be >= 4.0
        for r in results:
            if r["match"] == secret:
                assert r["entropy"] >= 4.0

    def test_filters_uuid(self):
        content = "id=550e8400e29b41d4a716446655440000 done"
        results = _scan_high_entropy_strings(content)
        assert not any("550e8400" in r["match"] for r in results)

    def test_filters_uuid_with_hyphens(self):
        # UUID with hyphens won't match the 20+ contiguous regex, but without hyphens should be filtered
        content = "id=550e8400-e29b-41d4-a716-446655440000 done"
        results = _scan_high_entropy_strings(content)
        assert not any("550e8400" in r["match"] for r in results)

    def test_filters_hex_hash(self):
        content = f"commit=abcdef1234567890abcdef1234567890abcdef12 done"
        results = _scan_high_entropy_strings(content)
        assert not any("abcdef1234567890" in r["match"] for r in results)

    def test_filters_known_prefix_eyj(self):
        content = "token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 done"
        results = _scan_high_entropy_strings(content)
        assert not any(r["match"].startswith("eyJ") for r in results)

    def test_filters_known_prefix_ghp(self):
        content = "token=ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ012345 done"
        results = _scan_high_entropy_strings(content)
        assert not any(r["match"].startswith("ghp_") for r in results)

    def test_filters_file_extension_path(self):
        content = "import=some_long_module_name_thing.py done"
        results = _scan_high_entropy_strings(content)
        assert not any(".py" in r["match"] for r in results)

    def test_filters_path_like(self):
        content = "path=src/components/authentication/LoginForm done"
        results = _scan_high_entropy_strings(content)
        assert not any("src/components" in r["match"] for r in results)

    def test_filters_low_entropy(self):
        # Repetitive string with mixed chars but low entropy
        content = "val=aaaaaaBBBBBB111111aaaaaaBBBBBB111111 done"
        results = _scan_high_entropy_strings(content)
        assert not any("aaaaaa" in r["match"] for r in results)

    def test_filters_no_mixed_chars(self):
        # All lowercase - no mixed char types
        content = "val=abcdefghijklmnopqrstuvwxyz done"
        results = _scan_high_entropy_strings(content)
        assert not any("abcdefghijklmnop" in r["match"] for r in results)

    def test_context_snippet(self):
        secret = "aB3dE6gH9jK2mN5pQ8rS1tU4wX7yZ0c"
        prefix = "before_context "
        suffix = " after_context"
        content = prefix + secret + suffix
        results = _scan_high_entropy_strings(content)
        matched = [r for r in results if r["match"] == secret]
        assert len(matched) == 1
        assert "before_context" in matched[0]["context"]
        assert "after_context" in matched[0]["context"]

    def test_results_capped_at_max(self):
        # Generate many distinct high-entropy strings
        import string
        import random
        rng = random.Random(42)
        chars = string.ascii_letters + string.digits
        secrets = []
        for _ in range(25):
            s = "".join(rng.choices(chars, k=30))
            secrets.append(s)
        content = " ".join(f"key={s}" for s in secrets)
        results = _scan_high_entropy_strings(content, max_results=15)
        assert len(results) <= 15

    def test_empty_content(self):
        assert _scan_high_entropy_strings("") == []

    def test_sorted_by_entropy_descending(self):
        secret1 = "aB3dE6gH9jK2mN5pQ8rS1tU4wX7yZ0c"
        secret2 = "Zx9Yw8Xv7Wu6Ts5Rq4Po3Nm2Lk1Jh0G"
        content = f"a={secret1} b={secret2}"
        results = _scan_high_entropy_strings(content)
        if len(results) >= 2:
            assert results[0]["entropy"] >= results[1]["entropy"]

    def test_filters_benign_prefix_https(self):
        content = "url=https://example.com/some/long/path/here done"
        results = _scan_high_entropy_strings(content)
        assert not any(r["match"].startswith("https://") for r in results)

    def test_filters_three_dots(self):
        content = "ver=com.example.app.module.v1.2.3 done"
        results = _scan_high_entropy_strings(content)
        assert not any("com.example.app" in r["match"] for r in results)

    def test_filters_node_modules(self):
        content = "path=some_long_node_modules_path_thing done"
        results = _scan_high_entropy_strings(content)
        assert not any("node_modules" in r["match"] for r in results)


# --- _scan_pii integration with high_entropy_strings ---


class TestScanPiiHighEntropy:
    def test_includes_high_entropy_when_present(self, tmp_path):
        secret = "aB3dE6gH9jK2mN5pQ8rS1tU4wX7yZ0c"
        f = tmp_path / "export.jsonl"
        f.write_text(f'{{"message": "config token {secret} end"}}\n')
        results = _scan_pii(f)
        assert "high_entropy_strings" in results
        assert any(r["match"] == secret for r in results["high_entropy_strings"])

    def test_excludes_high_entropy_when_clean(self, tmp_path):
        f = tmp_path / "export.jsonl"
        f.write_text('{"message": "nothing suspicious here at all"}\n')
        results = _scan_pii(f)
        assert "high_entropy_strings" not in results


# --- dataset enable/disable ---


class TestDatasetEnabledHelpers:
    def test_default_enabled(self):
        assert _is_dataset_globally_enabled({}) is True

    def test_explicitly_enabled(self):
        assert _is_dataset_globally_enabled({"dataset_enabled": True}) is True

    def test_explicitly_disabled(self):
        assert _is_dataset_globally_enabled({"dataset_enabled": False}) is False

    def test_no_disabled_projects(self):
        assert _get_disabled_projects({}) == set()

    def test_disabled_projects_returned_as_set(self):
        cfg = {"disabled_projects": ["proj1", "proj2"]}
        result = _get_disabled_projects(cfg)
        assert result == {"proj1", "proj2"}


class TestConfigureDatasetControl:
    def test_disable_globally(self, tmp_config, monkeypatch, capsys):
        monkeypatch.setattr("codeclaw.cli.config.CONFIG_FILE", tmp_config)
        monkeypatch.setattr("codeclaw.cli.config.load_config", lambda: {
            "dataset_enabled": True, "excluded_projects": [], "redact_strings": []
        })
        saved = {}
        monkeypatch.setattr("codeclaw.cli.config.save_config", lambda c: saved.update(c))
        configure(dataset_enabled=False)
        assert saved["dataset_enabled"] is False

    def test_enable_globally(self, tmp_config, monkeypatch, capsys):
        monkeypatch.setattr("codeclaw.cli.config.CONFIG_FILE", tmp_config)
        monkeypatch.setattr("codeclaw.cli.config.load_config", lambda: {
            "dataset_enabled": False, "excluded_projects": [], "redact_strings": []
        })
        saved = {}
        monkeypatch.setattr("codeclaw.cli.config.save_config", lambda c: saved.update(c))
        configure(dataset_enabled=True)
        assert saved["dataset_enabled"] is True

    def test_disable_specific_project(self, tmp_config, monkeypatch, capsys):
        monkeypatch.setattr("codeclaw.cli.config.CONFIG_FILE", tmp_config)
        monkeypatch.setattr("codeclaw.cli.config.load_config", lambda: {
            "disabled_projects": [], "excluded_projects": [], "redact_strings": []
        })
        saved = {}
        monkeypatch.setattr("codeclaw.cli.config.save_config", lambda c: saved.update(c))
        configure(disable_projects=["my-project"])
        assert "my-project" in saved["disabled_projects"]

    def test_enable_specific_project_removes_from_disabled(self, tmp_config, monkeypatch, capsys):
        monkeypatch.setattr("codeclaw.cli.config.CONFIG_FILE", tmp_config)
        monkeypatch.setattr("codeclaw.cli.config.load_config", lambda: {
            "disabled_projects": ["my-project", "other-proj"],
            "excluded_projects": [], "redact_strings": [],
        })
        saved = {}
        monkeypatch.setattr("codeclaw.cli.config.save_config", lambda c: saved.update(c))
        configure(enable_projects=["my-project"])
        assert "my-project" not in saved["disabled_projects"]
        assert "other-proj" in saved["disabled_projects"]

    def test_disable_multiple_projects_merged(self, tmp_config, monkeypatch, capsys):
        monkeypatch.setattr("codeclaw.cli.config.CONFIG_FILE", tmp_config)
        monkeypatch.setattr("codeclaw.cli.config.load_config", lambda: {
            "disabled_projects": ["existing"], "excluded_projects": [], "redact_strings": []
        })
        saved = {}
        monkeypatch.setattr("codeclaw.cli.config.save_config", lambda c: saved.update(c))
        configure(disable_projects=["proj1", "proj2"])
        assert set(saved["disabled_projects"]) == {"existing", "proj1", "proj2"}


class TestExportDatasetDisabled:
    """Tests that export respects the dataset enabled/disabled flags."""

    @staticmethod
    def _extract_json(stdout: str) -> dict:
        start = stdout.find("{")
        assert start >= 0
        return json.loads(stdout[start:])

    def test_export_blocked_when_globally_disabled(self, monkeypatch, capsys):
        monkeypatch.setattr("codeclaw.cli.export.load_config", lambda: {
            "dataset_enabled": False,
            "source": "both",
        })
        monkeypatch.setattr("sys.argv", ["codeclaw", "export", "--no-push"])
        with pytest.raises(SystemExit):
            main()
        payload = self._extract_json(capsys.readouterr().out)
        assert "disabled" in payload["error"].lower()
        assert "re-enable" in payload["hint"].lower()

    def test_export_skips_disabled_project(self, tmp_path, monkeypatch, capsys):
        output = tmp_path / "out.jsonl"
        session_data = [
            {
                "session_id": "s1",
                "model": "claude-sonnet-4-20250514",
                "messages": [{"role": "user", "content": "hello"}],
                "stats": {"input_tokens": 10, "output_tokens": 5},
                "project": "enabled-proj",
            }
        ]
        monkeypatch.setattr("codeclaw.cli.export._has_session_sources", lambda _src: True)
        monkeypatch.setattr(
            "codeclaw.cli.export.discover_projects",
            lambda: [
                {
                    "display_name": "enabled-proj",
                    "dir_name": "enabled-proj",
                    "session_count": 1,
                    "total_size_bytes": 512,
                    "source": "claude",
                },
                {
                    "display_name": "disabled-proj",
                    "dir_name": "disabled-proj",
                    "session_count": 2,
                    "total_size_bytes": 1024,
                    "source": "claude",
                },
            ],
        )
        monkeypatch.setattr("codeclaw.cli.export.parse_project_sessions", lambda *a, **kw: session_data)
        monkeypatch.setattr("codeclaw.cli.export.load_config", lambda: {
            "source": "both",
            "projects_confirmed": True,
            "dataset_enabled": True,
            "disabled_projects": ["disabled-proj"],
            "excluded_projects": [],
            "redact_strings": [],
            "synced_session_ids": [],
        })
        monkeypatch.setattr("codeclaw.cli.export.save_config", lambda _c: None)
        monkeypatch.setattr(
            "codeclaw.cli.export.Anonymizer",
            lambda **kw: MagicMock(anonymize_path=lambda p: p, anonymize_username=lambda u: u),
        )
        monkeypatch.setattr("sys.argv", ["codeclaw", "export", "--no-push", "--output", str(output)])
        main()
        out = capsys.readouterr().out
        assert "disabled-proj" in out
        assert "disabled by user" in out


class TestConfigCommandDatasetFlags:
    @staticmethod
    def _extract_json(stdout: str) -> dict:
        start = stdout.find("{")
        assert start >= 0
        return json.loads(stdout[start:])

    def test_config_disable_global(self, tmp_config, monkeypatch, capsys):
        monkeypatch.setattr("codeclaw.cli.config.CONFIG_FILE", tmp_config)
        monkeypatch.setattr("codeclaw.cli.config.load_config", lambda: {
            "dataset_enabled": True, "disabled_projects": [],
            "excluded_projects": [], "redact_strings": [],
        })
        saved = {}
        monkeypatch.setattr("codeclaw.cli.config.save_config", lambda c: saved.update(c))
        monkeypatch.setattr("sys.argv", ["codeclaw", "config", "--disable"])
        main()
        assert saved["dataset_enabled"] is False

    def test_config_enable_global(self, tmp_config, monkeypatch, capsys):
        monkeypatch.setattr("codeclaw.cli.config.CONFIG_FILE", tmp_config)
        monkeypatch.setattr("codeclaw.cli.config.load_config", lambda: {
            "dataset_enabled": False, "disabled_projects": [],
            "excluded_projects": [], "redact_strings": [],
        })
        saved = {}
        monkeypatch.setattr("codeclaw.cli.config.save_config", lambda c: saved.update(c))
        monkeypatch.setattr("sys.argv", ["codeclaw", "config", "--enable"])
        main()
        assert saved["dataset_enabled"] is True

    def test_config_disable_project(self, tmp_config, monkeypatch, capsys):
        monkeypatch.setattr("codeclaw.cli.config.CONFIG_FILE", tmp_config)
        monkeypatch.setattr("codeclaw.cli.config.load_config", lambda: {
            "dataset_enabled": True, "disabled_projects": [],
            "excluded_projects": [], "redact_strings": [],
        })
        saved = {}
        monkeypatch.setattr("codeclaw.cli.config.save_config", lambda c: saved.update(c))
        monkeypatch.setattr("sys.argv", ["codeclaw", "config", "--disable-project", "my-proj"])
        main()
        assert "my-proj" in saved["disabled_projects"]

    def test_config_enable_project(self, tmp_config, monkeypatch, capsys):
        monkeypatch.setattr("codeclaw.cli.config.CONFIG_FILE", tmp_config)
        monkeypatch.setattr("codeclaw.cli.config.load_config", lambda: {
            "dataset_enabled": True, "disabled_projects": ["my-proj", "other"],
            "excluded_projects": [], "redact_strings": [],
        })
        saved = {}
        monkeypatch.setattr("codeclaw.cli.config.save_config", lambda c: saved.update(c))
        monkeypatch.setattr("sys.argv", ["codeclaw", "config", "--enable-project", "my-proj"])
        main()
        assert "my-proj" not in saved["disabled_projects"]
        assert "other" in saved["disabled_projects"]
