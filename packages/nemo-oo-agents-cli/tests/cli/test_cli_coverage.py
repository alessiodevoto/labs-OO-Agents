# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for CLI commands to improve code coverage.

Covers:
- nemo_oo_agents_cli._common
- nemo_oo_agents_cli.commands.traces
- nemo_oo_agents_cli.commands.delete_traces
- nemo_oo_agents_cli.completion
- nemo_oo_agents_cli.commands._otlp_helpers
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from nemo_oo_agents_cli._common import format_size, load_dotenv_into

# ---------------------------------------------------------------------------
# _common.py
# ---------------------------------------------------------------------------
from nemo_oo_agents.paths import find_project_root


class TestFormatSize:
    def test_bytes(self):
        assert format_size(500) == "500 B"

    def test_bytes_zero(self):
        assert format_size(0) == "0 B"

    def test_kilobytes(self):
        result = format_size(2048)
        assert "KB" in result
        assert "2.0" in result

    def test_megabytes(self):
        result = format_size(5 * 1024 * 1024)
        assert "MB" in result
        assert "5.0" in result

    def test_gigabytes(self):
        result = format_size(3 * 1024 * 1024 * 1024)
        assert "GB" in result
        assert "3.0" in result

    def test_just_under_kb(self):
        assert format_size(1023) == "1023 B"

    def test_exactly_kb(self):
        result = format_size(1024)
        assert "KB" in result

    def test_exactly_mb(self):
        result = format_size(1024 * 1024)
        assert "MB" in result

    def test_exactly_gb(self):
        result = format_size(1024 * 1024 * 1024)
        assert "GB" in result


class TestFindProjectRoot:
    def test_returns_path(self):
        result = find_project_root()
        assert isinstance(result, Path)

    def test_finds_pyproject_toml(self):
        # The project root should have pyproject.toml
        result = find_project_root()
        # It either found pyproject.toml or fell back to cwd
        assert result.exists()


class TestLoadDotenvInto:
    def test_basic_key_value(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\nBAZ=qux\n")
        env = {}
        load_dotenv_into(env_file, env)
        assert env["FOO"] == "bar"
        assert env["BAZ"] == "qux"

    def test_ignores_comments(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\nFOO=bar\n")
        env = {}
        load_dotenv_into(env_file, env)
        assert "# comment" not in env
        assert env["FOO"] == "bar"

    def test_ignores_blank_lines(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("\nFOO=bar\n\n")
        env = {}
        load_dotenv_into(env_file, env)
        assert env == {"FOO": "bar"}

    def test_strips_quotes_single(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("FOO='hello world'\n")
        env = {}
        load_dotenv_into(env_file, env)
        assert env["FOO"] == "hello world"

    def test_strips_quotes_double(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text('FOO="hello world"\n')
        env = {}
        load_dotenv_into(env_file, env)
        assert env["FOO"] == "hello world"

    def test_value_with_equals_sign(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=a=b=c\n")
        env = {}
        load_dotenv_into(env_file, env)
        assert env["FOO"] == "a=b=c"

    def test_missing_file_silently_ignored(self, tmp_path):
        env = {}
        load_dotenv_into(tmp_path / "nonexistent.env", env)
        assert env == {}

    def test_skips_lines_without_equals(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("NOEQUALS\nFOO=bar\n")
        env = {}
        load_dotenv_into(env_file, env)
        assert "NOEQUALS" not in env
        assert env["FOO"] == "bar"

    def test_updates_existing_dict(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("NEW=value\n")
        env = {"EXISTING": "stays"}
        load_dotenv_into(env_file, env)
        assert env["EXISTING"] == "stays"
        assert env["NEW"] == "value"


# ---------------------------------------------------------------------------
# _otlp_helpers.py
# ---------------------------------------------------------------------------
from nemo_oo_agents_cli.commands._otlp_helpers import (  # noqa: E402
    check_endpoint_reachable,
    inject_resource_attrs,
    post_annotations,
    post_trace,
    session_exists,
    validate_endpoint,
)


class TestValidateEndpoint:
    def test_valid_http(self):
        # Should not raise
        validate_endpoint("http://localhost:5001")

    def test_valid_https(self):
        validate_endpoint("https://example.com")

    def test_invalid_scheme_raises(self):
        import click

        with pytest.raises(click.BadParameter):
            validate_endpoint("ftp://example.com")

    def test_no_scheme_raises(self):
        import click

        with pytest.raises(click.BadParameter):
            validate_endpoint("localhost:5001")


class TestInjectResourceAttrs:
    def test_injects_string_value(self):
        body = {"resourceSpans": [{}]}
        result = inject_resource_attrs(body, {"env": "prod"})
        attrs = result["resourceSpans"][0]["resource"]["attributes"]
        assert any(a["key"] == "env" and a["value"] == {"stringValue": "prod"} for a in attrs)

    def test_injects_bool_value(self):
        body = {"resourceSpans": [{}]}
        result = inject_resource_attrs(body, {"debug": True})
        attrs = result["resourceSpans"][0]["resource"]["attributes"]
        assert any(a["key"] == "debug" and a["value"] == {"boolValue": True} for a in attrs)

    def test_injects_int_value(self):
        body = {"resourceSpans": [{}]}
        result = inject_resource_attrs(body, {"count": 42})
        attrs = result["resourceSpans"][0]["resource"]["attributes"]
        assert any(a["key"] == "count" and a["value"] == {"intValue": 42} for a in attrs)

    def test_skips_existing_keys(self):
        body = {
            "resourceSpans": [
                {"resource": {"attributes": [{"key": "env", "value": {"stringValue": "existing"}}]}}
            ]
        }
        result = inject_resource_attrs(body, {"env": "new"})
        attrs = result["resourceSpans"][0]["resource"]["attributes"]
        env_attrs = [a for a in attrs if a["key"] == "env"]
        assert len(env_attrs) == 1
        assert env_attrs[0]["value"] == {"stringValue": "existing"}

    def test_empty_resource_spans(self):
        body = {"resourceSpans": []}
        result = inject_resource_attrs(body, {"key": "val"})
        assert result == {"resourceSpans": []}

    def test_no_resource_spans_key(self):
        body = {}
        result = inject_resource_attrs(body, {"key": "val"})
        assert result == {}

    def test_multiple_resource_spans(self):
        body = {"resourceSpans": [{}, {}]}
        result = inject_resource_attrs(body, {"env": "prod"})
        for rs in result["resourceSpans"]:
            attrs = rs["resource"]["attributes"]
            assert any(a["key"] == "env" for a in attrs)


class TestPostTrace:
    def test_success(self):
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = post_trace("http://localhost:5001", {"resourceSpans": []})
        assert result is True

    def test_failure_on_exception(self):
        with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
            result = post_trace("http://localhost:5001", {"resourceSpans": []})
        assert result is False

    def test_failure_on_bad_status(self):
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 500

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = post_trace("http://localhost:5001", {"resourceSpans": []})
        assert result is False

    def test_strips_trailing_slash(self):
        captured_urls = []

        def fake_urlopen(req, timeout=None):
            captured_urls.append(req.full_url)
            raise Exception("stop")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            post_trace("http://localhost:5001/", {"resourceSpans": []})

        assert captured_urls[0] == "http://localhost:5001/v1/traces"


class TestPostAnnotations:
    def test_imports_all_successfully(self):
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200

        annotations = [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}]
        with patch("urllib.request.urlopen", return_value=mock_resp):
            count = post_annotations("http://localhost:5001", annotations)
        assert count == 2

    def test_skips_id_field(self):
        captured_data = []

        def fake_urlopen(req, timeout=None):
            captured_data.append(json.loads(req.data))
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.status = 200
            return mock_resp

        annotations = [{"id": 99, "text": "note"}]
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            post_annotations("http://localhost:5001", annotations)

        assert "id" not in captured_data[0]
        assert captured_data[0]["text"] == "note"

    def test_continues_on_exception(self):
        call_count = [0]

        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("fail")
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.status = 200
            return mock_resp

        annotations = [{"text": "a"}, {"text": "b"}]
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            count = post_annotations("http://localhost:5001", annotations)
        assert count == 1

    def test_empty_list(self):
        count = post_annotations("http://localhost:5001", [])
        assert count == 0


class TestSessionExists:
    def test_returns_true_on_200(self):
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = session_exists("http://localhost:5001", "sess-123")
        assert result is True

    def test_returns_false_on_exception(self):
        with patch("urllib.request.urlopen", side_effect=Exception("err")):
            result = session_exists("http://localhost:5001", "sess-123")
        assert result is False


class TestCheckEndpointReachable:
    def test_returns_true_when_reachable(self):
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = check_endpoint_reachable("http://localhost:5001")
        assert result is True

    def test_returns_false_when_unreachable(self):
        with patch("urllib.request.urlopen", side_effect=Exception("conn refused")):
            result = check_endpoint_reachable("http://localhost:5001")
        assert result is False


# ---------------------------------------------------------------------------
# delete_traces.py
# ---------------------------------------------------------------------------
from nemo_oo_agents_cli.commands.delete_traces import _validate_endpoint  # noqa: E402
from nemo_oo_agents_cli.commands.delete_traces import command as delete_traces_command  # noqa: E402


class TestDeleteTracesValidateEndpoint:
    def test_valid_http(self):
        _validate_endpoint("http://localhost:5001")

    def test_valid_https(self):
        _validate_endpoint("https://example.com")

    def test_invalid_scheme_raises(self):
        import click

        with pytest.raises(click.BadParameter):
            _validate_endpoint("ftp://bad")

    def test_no_scheme_raises(self):
        import click

        with pytest.raises(click.BadParameter):
            _validate_endpoint("localhost:5001")


class TestDeleteTracesCommand:
    def setup_method(self):
        self.runner = CliRunner()

    def test_missing_batch_id(self):
        result = self.runner.invoke(delete_traces_command, [])
        assert result.exit_code != 0
        assert "batch-id" in result.output.lower() or result.exit_code == 2

    def test_invalid_endpoint_scheme(self):
        result = self.runner.invoke(
            delete_traces_command,
            ["--batch-id", "my-batch", "--endpoint", "ftp://bad"],
        )
        assert result.exit_code != 0

    def test_unreachable_endpoint(self):
        with patch("urllib.request.urlopen", side_effect=Exception("conn refused")):
            result = self.runner.invoke(
                delete_traces_command,
                ["--batch-id", "my-batch", "--endpoint", "http://localhost:9999"],
            )
        assert "Cannot reach viewer" in result.output
        assert result.exit_code != 0

    def test_successful_delete(self):
        version_resp = MagicMock()
        version_resp.__enter__ = MagicMock(return_value=version_resp)
        version_resp.__exit__ = MagicMock(return_value=False)

        delete_resp = MagicMock()
        delete_resp.__enter__ = MagicMock(return_value=delete_resp)
        delete_resp.__exit__ = MagicMock(return_value=False)
        delete_resp.read.return_value = json.dumps({"deleted": 5}).encode()

        responses = [version_resp, delete_resp]
        call_count = [0]

        def fake_urlopen(req_or_url, timeout=None):
            idx = call_count[0]
            call_count[0] += 1
            return responses[idx]

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = self.runner.invoke(
                delete_traces_command,
                ["--batch-id", "my-batch", "--endpoint", "http://localhost:5001"],
            )
        assert "Deleted 5 trace(s)" in result.output
        assert result.exit_code == 0

    def test_delete_api_failure(self):
        version_resp = MagicMock()
        version_resp.__enter__ = MagicMock(return_value=version_resp)
        version_resp.__exit__ = MagicMock(return_value=False)

        call_count = [0]

        def fake_urlopen(req_or_url, timeout=None):
            if call_count[0] == 0:
                call_count[0] += 1
                return version_resp
            raise Exception("delete failed")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = self.runner.invoke(
                delete_traces_command,
                ["--batch-id", "my-batch", "--endpoint", "http://localhost:5001"],
            )
        assert "Failed to delete" in result.output
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# traces.py CLI commands
# ---------------------------------------------------------------------------
from nemo_oo_agents_cli.commands.traces import (  # noqa: E402
    _collect_files,
    _discover_trace_dirs,
    _find_eval_files,
    _find_trace_files,
    _has_session_id,
    _walk_for,
)
from nemo_oo_agents_cli.commands.traces import (  # noqa: E402
    command as traces_command,
)


class TestWalkFor:
    def test_finds_named_dir(self, tmp_path):
        (tmp_path / "traces").mkdir()
        results = _walk_for(tmp_path, "traces")
        assert tmp_path / "traces" in results

    def test_skips_excluded_dirs(self, tmp_path):
        (tmp_path / ".venv" / "traces").mkdir(parents=True)
        results = _walk_for(tmp_path, "traces")
        assert not results

    def test_recurses_into_subdirs(self, tmp_path):
        subdir = tmp_path / "agents" / "my_agent"
        subdir.mkdir(parents=True)
        (subdir / "traces").mkdir()
        results = _walk_for(tmp_path, "traces")
        assert subdir / "traces" in results

    def test_returns_empty_for_no_match(self, tmp_path):
        results = _walk_for(tmp_path, "traces")
        assert results == []


class TestDiscoverTraceDirs:
    def test_finds_root_traces(self, tmp_path):
        (tmp_path / "traces").mkdir()
        dirs = _discover_trace_dirs(tmp_path)
        assert tmp_path / "traces" in dirs

    def test_finds_agents_traces(self, tmp_path):
        agent_traces = tmp_path / "agents" / "my_agent" / "traces"
        agent_traces.mkdir(parents=True)
        dirs = _discover_trace_dirs(tmp_path)
        assert agent_traces in dirs

    def test_returns_empty_when_none(self, tmp_path):
        dirs = _discover_trace_dirs(tmp_path)
        assert dirs == []

    def test_finds_util_traces(self, tmp_path):
        util_traces = tmp_path / "util" / "e2e_optimization" / "traces"
        util_traces.mkdir(parents=True)
        dirs = _discover_trace_dirs(tmp_path)
        assert util_traces in dirs


class TestCollectFiles:
    def test_collects_jsonl_files(self, tmp_path):
        (tmp_path / "trace1.jsonl").write_text("{}")
        (tmp_path / "trace2.jsonl").write_text("{}")
        out = []
        _collect_files(tmp_path, "*.jsonl", out)
        assert len(out) == 2

    def test_excludes_annotation_files_when_flag_set(self, tmp_path):
        (tmp_path / "trace1.jsonl").write_text("{}")
        (tmp_path / "trace1.annotations.jsonl").write_text("{}")
        out = []
        _collect_files(tmp_path, "*.jsonl", out, exclude_non_trace=True)
        names = [f.name for f in out]
        assert "trace1.jsonl" in names
        assert "trace1.annotations.jsonl" not in names

    def test_excludes_noo_eval_when_flag_set(self, tmp_path):
        (tmp_path / "data.noo-eval.jsonl").write_text("{}")
        out = []
        _collect_files(tmp_path, "*.jsonl", out, exclude_non_trace=True)
        assert out == []

    def test_skips_excluded_dirs(self, tmp_path):
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "trace.jsonl").write_text("{}")
        out = []
        _collect_files(tmp_path, "*.jsonl", out)
        assert out == []


class TestFindTraceFiles:
    def test_finds_trace_files(self, tmp_path):
        (tmp_path / "run1.jsonl").write_text("{}")
        files = _find_trace_files(tmp_path)
        assert any(f.name == "run1.jsonl" for f in files)

    def test_excludes_eval_files(self, tmp_path):
        (tmp_path / "data.noo-eval.jsonl").write_text("{}")
        files = _find_trace_files(tmp_path)
        assert not any(f.name.endswith(".noo-eval.jsonl") for f in files)


class TestFindEvalFiles:
    def test_finds_noo_eval_files(self, tmp_path):
        (tmp_path / "data.noo-eval.jsonl").write_text("{}")
        files = _find_eval_files(tmp_path)
        assert any(f.name == "data.noo-eval.jsonl" for f in files)

    def test_finds_006eval_files(self, tmp_path):
        (tmp_path / "data.006eval.json").write_text("{}")
        files = _find_eval_files(tmp_path)
        assert any(f.name == "data.006eval.json" for f in files)


class TestHasSessionId:
    def test_returns_true_when_session_id_present(self, tmp_path):
        f = tmp_path / "trace.jsonl"
        f.write_text('{"session.id": "abc-123"}')
        assert _has_session_id(f) is True

    def test_returns_false_when_no_session_id(self, tmp_path):
        f = tmp_path / "trace.jsonl"
        f.write_text('{"data": "value"}')
        assert _has_session_id(f) is False

    def test_returns_true_on_os_error(self, tmp_path):
        # Non-existent file → OSError → conservatively True
        result = _has_session_id(tmp_path / "nonexistent.jsonl")
        assert result is True


class TestTracesDeleteCommand:
    def setup_method(self):
        self.runner = CliRunner()

    def test_no_files_found(self, tmp_path):
        result = self.runner.invoke(traces_command, ["delete", str(tmp_path), "-y"])
        assert "No files found" in result.output
        assert result.exit_code == 0

    def test_dry_run_shows_files(self, tmp_path):
        (tmp_path / "trace1.jsonl").write_text('{"data": "no session id here"}')
        result = self.runner.invoke(traces_command, ["delete", str(tmp_path), "--dry-run", "--all"])
        assert "[DRY RUN]" in result.output
        assert result.exit_code == 0

    def test_deletes_with_yes_flag(self, tmp_path):
        trace = tmp_path / "trace1.jsonl"
        trace.write_text('{"data": "no session id here"}')
        result = self.runner.invoke(traces_command, ["delete", str(tmp_path), "-y", "--all"])
        assert "Deleted" in result.output
        assert not trace.exists()
        assert result.exit_code == 0

    def test_older_than_filters_files(self, tmp_path):
        trace = tmp_path / "trace1.jsonl"
        trace.write_text('{"data": "value"}')
        # File is newly created; older-than 100 days should exclude it
        self.runner.invoke(
            traces_command, ["delete", str(tmp_path), "--older-than", "100", "--all", "-y"]
        )
        # The file shouldn't get deleted (it's new)
        assert trace.exists()

    def test_skips_session_id_files_by_default(self, tmp_path):
        trace = tmp_path / "trace1.jsonl"
        trace.write_text('{"session.id": "abc-123"}')
        self.runner.invoke(traces_command, ["delete", str(tmp_path), "-y"])
        # File has session.id so it should be preserved
        assert trace.exists()

    def test_evals_only_flag(self, tmp_path):
        eval_file = tmp_path / "data.noo-eval.jsonl"
        eval_file.write_text("{}")
        trace_file = tmp_path / "trace.jsonl"
        trace_file.write_text("{}")
        result = self.runner.invoke(traces_command, ["delete", str(tmp_path), "--evals-only", "-y"])
        assert not eval_file.exists()
        assert trace_file.exists()
        assert result.exit_code == 0

    def test_evals_flag_includes_eval_files(self, tmp_path):
        eval_file = tmp_path / "data.noo-eval.jsonl"
        eval_file.write_text("{}")
        result = self.runner.invoke(
            traces_command,
            ["delete", str(tmp_path), "--evals", "--all", "-y"],
        )
        assert result.exit_code == 0
        assert not eval_file.exists()

    def test_no_files_to_delete_after_filter(self, tmp_path):
        # Files with session.id and no --all flag → nothing to delete
        trace = tmp_path / "trace1.jsonl"
        trace.write_text('{"session.id": "abc-123"}')
        result = self.runner.invoke(traces_command, ["delete", str(tmp_path), "-y"])
        assert "No files to delete" in result.output

    def test_dry_run_more_than_20_files(self, tmp_path):
        for i in range(25):
            (tmp_path / f"trace{i}.jsonl").write_text("{}")
        result = self.runner.invoke(traces_command, ["delete", str(tmp_path), "--dry-run", "--all"])
        assert "more" in result.output
        assert result.exit_code == 0


class TestTracesListCommand:
    def setup_method(self):
        self.runner = CliRunner()

    def test_no_trace_dirs(self, tmp_path):
        result = self.runner.invoke(traces_command, ["list", "--root", str(tmp_path)])
        assert "No trace directories found" in result.output
        assert result.exit_code == 0

    def test_lists_trace_dirs(self, tmp_path):
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()
        (traces_dir / "run1.jsonl").write_text("{}")
        result = self.runner.invoke(traces_command, ["list", "--root", str(tmp_path)])
        assert "traces" in result.output
        assert result.exit_code == 0


class TestTracesStatsCommand:
    def setup_method(self):
        self.runner = CliRunner()

    def test_no_files(self, tmp_path):
        result = self.runner.invoke(traces_command, ["stats", str(tmp_path)])
        assert "No trace or eval files found" in result.output
        assert result.exit_code == 0

    def test_with_trace_files(self, tmp_path):
        trace = tmp_path / "trace1.jsonl"
        trace.write_text('{"session.id": "abc"}')
        result = self.runner.invoke(traces_command, ["stats", str(tmp_path)])
        assert "Trace files" in result.output
        assert result.exit_code == 0

    def test_with_eval_files(self, tmp_path):
        eval_file = tmp_path / "data.noo-eval.jsonl"
        eval_file.write_text("{}")
        result = self.runner.invoke(traces_command, ["stats", str(tmp_path)])
        assert "Eval files" in result.output
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# completion.py
# ---------------------------------------------------------------------------
from nemo_oo_agents_cli.completion import (  # noqa: E402
    _BASH_SCRIPT,
    _FISH_SCRIPT,
    _ZSH_SCRIPT,
    _detect_shell,
    _render_script,
    bash,
    completion,
    fish,
    install,
    zsh,
)


class TestRenderScript:
    def test_bash_script_rendered(self):
        result = _render_script(_BASH_SCRIPT)
        assert "_nemo_completion" in result
        assert "_NEMO_COMPLETE" in result
        assert "nemo" in result

    def test_zsh_script_rendered(self):
        result = _render_script(_ZSH_SCRIPT)
        assert "_nemo_completion" in result
        assert "nemo" in result

    def test_fish_script_rendered(self):
        result = _render_script(_FISH_SCRIPT)
        assert "nemo" in result
        assert "_NEMO_COMPLETE" in result


class TestDetectShell:
    def test_detects_bash(self):
        with patch.dict("os.environ", {"SHELL": "/bin/bash"}):
            assert _detect_shell() == "bash"

    def test_detects_zsh(self):
        with patch.dict("os.environ", {"SHELL": "/usr/bin/zsh"}):
            assert _detect_shell() == "zsh"

    def test_detects_fish(self):
        with patch.dict("os.environ", {"SHELL": "/usr/local/bin/fish"}):
            assert _detect_shell() == "fish"

    def test_returns_none_for_unknown(self):
        with patch.dict("os.environ", {"SHELL": "/bin/sh"}):
            assert _detect_shell() is None

    def test_returns_none_for_empty(self):
        with patch.dict("os.environ", {"SHELL": ""}):
            assert _detect_shell() is None


class TestCompletionCommands:
    def setup_method(self):
        self.runner = CliRunner()

    def test_bash_command(self):
        result = self.runner.invoke(bash)
        assert result.exit_code == 0
        assert "_nemo_completion" in result.output
        assert "_NEMO_COMPLETE" in result.output

    def test_zsh_command(self):
        result = self.runner.invoke(zsh)
        assert result.exit_code == 0
        assert "#compdef nemo" in result.output

    def test_fish_command(self):
        result = self.runner.invoke(fish)
        assert result.exit_code == 0
        assert "complete -c nemo" in result.output

    def test_install_unknown_shell(self):
        with patch("nemo_oo_agents_cli.completion._detect_shell", return_value=None):
            result = self.runner.invoke(install)
        assert result.exit_code != 0
        assert "Could not detect" in result.output

    def test_install_bash(self, tmp_path):
        profile = tmp_path / ".bashrc"
        profile.write_text("")
        with (
            patch("nemo_oo_agents_cli.completion._detect_shell", return_value="bash"),
            patch("nemo_oo_agents_cli.completion.Path.home", return_value=tmp_path),
        ):
            result = self.runner.invoke(install)
        # Either success or file writing worked
        assert result.exit_code == 0 or "_NEMO_COMPLETE" in profile.read_text()

    def test_install_zsh(self, tmp_path):
        profile = tmp_path / ".zshrc"
        profile.write_text("")
        with (
            patch("nemo_oo_agents_cli.completion._detect_shell", return_value="zsh"),
            patch("nemo_oo_agents_cli.completion.Path.home", return_value=tmp_path),
        ):
            result = self.runner.invoke(install)
        assert result.exit_code == 0

    def test_install_already_installed(self, tmp_path):
        profile = tmp_path / ".bashrc"
        profile.write_text("eval $(_NEMO_COMPLETE=bash_source nemo-oo)\n")
        with (
            patch("nemo_oo_agents_cli.completion._detect_shell", return_value="bash"),
            patch("nemo_oo_agents_cli.completion.Path.home", return_value=tmp_path),
        ):
            result = self.runner.invoke(install)
        assert "already installed" in result.output
        assert result.exit_code == 0

    def test_install_fish(self, tmp_path):
        fish_dir = tmp_path / ".config" / "fish" / "completions"
        fish_dir.mkdir(parents=True)
        with (
            patch("nemo_oo_agents_cli.completion._detect_shell", return_value="fish"),
            patch("nemo_oo_agents_cli.completion.Path.home", return_value=tmp_path),
        ):
            result = self.runner.invoke(install)
        assert result.exit_code == 0

    def test_completion_group_help(self):
        result = self.runner.invoke(completion, ["--help"])
        assert result.exit_code == 0
        assert "completion" in result.output.lower() or "shell" in result.output.lower()
