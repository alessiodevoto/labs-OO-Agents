"""Tests for import-traces and import-harbor CLI commands.

Uses Click's CliRunner with mocked network helpers so tests run offline.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OTLP_TRACE = json.dumps(
    {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [],
            }
        ]
    }
)

_LEGACY_TRACE = json.dumps({"span_id": "abc", "trace_id": "xyz"})

_ANNOTATION_LINE = json.dumps(
    {"annotations": [{"spanId": "abc", "text": "good", "color": "green"}]}
)


# ---------------------------------------------------------------------------
# import_traces helpers
# ---------------------------------------------------------------------------


class TestFindTraceFiles:
    def test_single_file(self, tmp_path):
        f = tmp_path / "run.jsonl"
        f.write_text(_OTLP_TRACE)

        from nemo_oo_agents_cli.commands.import_traces import _find_trace_files

        result = _find_trace_files(f)
        assert result == [f]

    def test_directory_finds_jsonl(self, tmp_path):
        f = tmp_path / "run.jsonl"
        f.write_text(_OTLP_TRACE)

        from nemo_oo_agents_cli.commands.import_traces import _find_trace_files

        result = _find_trace_files(tmp_path)
        assert f in result

    def test_nonexistent_path_returns_empty(self, tmp_path):
        from nemo_oo_agents_cli.commands.import_traces import _find_trace_files

        result = _find_trace_files(tmp_path / "does_not_exist")
        assert result == []

    def test_nested_files_found(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        f = sub / "trace.jsonl"
        f.write_text(_OTLP_TRACE)

        from nemo_oo_agents_cli.commands.import_traces import _find_trace_files

        result = _find_trace_files(tmp_path)
        assert f in result

    def test_no_duplicates(self, tmp_path):
        f = tmp_path / "run.jsonl"
        f.write_text(_OTLP_TRACE)

        from nemo_oo_agents_cli.commands.import_traces import _find_trace_files

        result = _find_trace_files(tmp_path)
        assert len(result) == len({r.resolve() for r in result})


class TestDetectFormat:
    def test_otlp(self):
        from nemo_oo_agents_cli.commands.import_traces import _detect_format

        assert _detect_format({"resourceSpans": []}) == "otlp"

    def test_legacy_span_id(self):
        from nemo_oo_agents_cli.commands.import_traces import _detect_format

        assert _detect_format({"span_id": "abc"}) == "legacy"

    def test_legacy_trace_id(self):
        from nemo_oo_agents_cli.commands.import_traces import _detect_format

        assert _detect_format({"trace_id": "xyz"}) == "legacy"

    def test_unknown(self):
        from nemo_oo_agents_cli.commands.import_traces import _detect_format

        assert _detect_format({"some_key": "value"}) == "unknown"


class TestSessionIdFromFilename:
    def test_strips_jsonl(self):
        from nemo_oo_agents_cli.commands.import_traces import _session_id_from_filename

        p = Path("/some/dir/my_trace.jsonl")
        assert _session_id_from_filename(p) == "my_trace"

    def test_no_known_extension(self):
        from nemo_oo_agents_cli.commands.import_traces import _session_id_from_filename

        p = Path("/some/dir/my_trace.txt")
        assert _session_id_from_filename(p) == "my_trace"


# ---------------------------------------------------------------------------
# import_traces CLI command tests
# ---------------------------------------------------------------------------

_HELPERS_PATH = "nemo_oo_agents_cli.commands.import_traces"


def _mock_helpers(
    endpoint_reachable=True,
    session_exists_val=False,
    post_trace_val=True,
    post_annotations_val=0,
):
    return patch.multiple(
        _HELPERS_PATH,
        validate_endpoint=MagicMock(),
        check_endpoint_reachable=MagicMock(return_value=endpoint_reachable),
        session_exists=MagicMock(return_value=session_exists_val),
        post_trace=MagicMock(return_value=post_trace_val),
        inject_resource_attrs=MagicMock(side_effect=lambda body, attrs: body),
        post_annotations=MagicMock(return_value=post_annotations_val),
    )


class TestImportTracesCommand:
    def _get_command(self):
        from nemo_oo_agents_cli.commands.import_traces import command

        return command

    def test_no_trace_files_exits_1(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()

        runner = CliRunner()
        with _mock_helpers():
            result = runner.invoke(self._get_command(), [str(empty)])
        assert result.exit_code == 1
        assert "No trace files" in result.output

    def test_endpoint_unreachable_exits_1(self, tmp_path):
        f = tmp_path / "run.jsonl"
        f.write_text(_OTLP_TRACE)

        runner = CliRunner()
        with _mock_helpers(endpoint_reachable=False):
            result = runner.invoke(self._get_command(), [str(f)])
        assert result.exit_code == 1
        assert "Cannot reach" in result.output

    def test_successful_import(self, tmp_path):
        f = tmp_path / "session1.jsonl"
        f.write_text(_OTLP_TRACE)

        runner = CliRunner()
        with _mock_helpers():
            result = runner.invoke(
                self._get_command(),
                [str(f), "--endpoint", "http://localhost:5001"],
            )
        assert result.exit_code == 0
        assert "1 imported" in result.output

    def test_session_already_exists_skipped(self, tmp_path):
        f = tmp_path / "session1.jsonl"
        f.write_text(_OTLP_TRACE)

        runner = CliRunner()
        with _mock_helpers(session_exists_val=True):
            result = runner.invoke(self._get_command(), [str(f)])
        assert result.exit_code == 0
        assert "already exists" in result.output

    def test_custom_batch_id(self, tmp_path):
        f = tmp_path / "session1.jsonl"
        f.write_text(_OTLP_TRACE)

        runner = CliRunner()
        with _mock_helpers():
            result = runner.invoke(
                self._get_command(),
                [str(f), "--batch-id", "my-batch-42"],
            )
        assert result.exit_code == 0
        assert "my-batch-42" in result.output

    def test_legacy_format_reported_as_error(self, tmp_path):
        f = tmp_path / "legacy.jsonl"
        f.write_text(_LEGACY_TRACE)

        runner = CliRunner()
        with _mock_helpers():
            result = runner.invoke(self._get_command(), [str(f)])
        assert result.exit_code == 0
        # Legacy format should be reported or silently skipped (0 imported)
        assert "0 imported" in result.output or "skipped" in result.output

    def test_post_trace_failure_recorded(self, tmp_path):
        f = tmp_path / "session1.jsonl"
        f.write_text(_OTLP_TRACE)

        runner = CliRunner()
        with _mock_helpers(post_trace_val=False):
            result = runner.invoke(self._get_command(), [str(f)])
        assert result.exit_code == 0
        # The error should appear in output
        assert "failed to post" in result.output or "0 imported" in result.output

    def test_annotations_imported(self, tmp_path):
        f = tmp_path / "session1.jsonl"
        # Write an OTLP trace line then an annotation line
        f.write_text(_OTLP_TRACE + "\n" + _ANNOTATION_LINE + "\n")

        runner = CliRunner()
        with _mock_helpers(post_annotations_val=1):
            result = runner.invoke(self._get_command(), [str(f)])
        assert result.exit_code == 0
        assert "annotation" in result.output

    def test_empty_lines_skipped(self, tmp_path):
        f = tmp_path / "session1.jsonl"
        f.write_text("\n\n" + _OTLP_TRACE + "\n\n")

        runner = CliRunner()
        with _mock_helpers():
            result = runner.invoke(self._get_command(), [str(f)])
        assert result.exit_code == 0
        assert "1 imported" in result.output

    def test_invalid_json_lines_skipped(self, tmp_path):
        f = tmp_path / "session1.jsonl"
        f.write_text("not-valid-json\n" + _OTLP_TRACE)

        runner = CliRunner()
        with _mock_helpers():
            result = runner.invoke(self._get_command(), [str(f)])
        assert result.exit_code == 0

    def test_view_url_printed(self, tmp_path):
        f = tmp_path / "session1.jsonl"
        f.write_text(_OTLP_TRACE)

        runner = CliRunner()
        with _mock_helpers():
            result = runner.invoke(
                self._get_command(),
                [str(f), "--batch-id", "my-run"],
            )
        assert result.exit_code == 0
        assert "View at:" in result.output

    def test_unknown_format_lines_skipped(self, tmp_path):
        f = tmp_path / "session1.jsonl"
        # A JSON object that is neither OTLP nor legacy
        f.write_text(json.dumps({"random": "data"}))

        runner = CliRunner()
        with _mock_helpers():
            result = runner.invoke(self._get_command(), [str(f)])
        assert result.exit_code == 0
        # Nothing imported, nothing failed
        assert "0 imported" in result.output


# ---------------------------------------------------------------------------
# import_harbor helpers
# ---------------------------------------------------------------------------

_HARBOR_HELPERS_PATH = "nemo_oo_agents_cli.commands.import_harbor"


def _mock_harbor_helpers(
    endpoint_reachable=True,
    session_exists_val=False,
    post_trace_val=True,
):
    return patch.multiple(
        _HARBOR_HELPERS_PATH,
        validate_endpoint=MagicMock(),
        check_endpoint_reachable=MagicMock(return_value=endpoint_reachable),
        session_exists=MagicMock(return_value=session_exists_val),
        post_trace=MagicMock(return_value=post_trace_val),
        inject_resource_attrs=MagicMock(side_effect=lambda body, attrs: body),
    )


def _make_harbor_job(tmp_path, trial_name="trial-001", score=1.0, task="task1"):
    """Create a minimal Harbor job directory structure."""
    job_dir = tmp_path / "my-job"
    job_dir.mkdir()

    trial_dir = job_dir / trial_name
    trial_dir.mkdir()

    verifier_dir = trial_dir / "verifier"
    verifier_dir.mkdir()
    (verifier_dir / "reward.json").write_text(json.dumps({"score": score}))

    trial_result = {
        "trial_name": trial_name,
        "task_name": task,
        "agent_info": {"name": "TestAgent"},
    }
    (trial_dir / "result.json").write_text(json.dumps(trial_result))

    job_result = {
        "stats": {
            "evals": {
                "MyEval__train": {"total": 1, "passed": 1},
            }
        }
    }
    (job_dir / "result.json").write_text(json.dumps(job_result))

    artifacts_dir = trial_dir / "artifacts" / "traces"
    artifacts_dir.mkdir(parents=True)
    trace_file = artifacts_dir / "run.jsonl"
    trace_file.write_text(_OTLP_TRACE)

    return job_dir, trace_file


class TestFindHarborTraces:
    def test_finds_jsonl_under_artifacts(self, tmp_path):
        job_dir, trace_file = _make_harbor_job(tmp_path)

        from nemo_oo_agents_cli.commands.import_harbor import _find_harbor_traces

        result = _find_harbor_traces(job_dir)
        assert trace_file in result

    def test_empty_dir_returns_empty(self, tmp_path):
        from nemo_oo_agents_cli.commands.import_harbor import _find_harbor_traces

        result = _find_harbor_traces(tmp_path)
        assert result == []


class TestReadJson:
    def test_reads_valid_json(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text('{"key": "value"}')

        from nemo_oo_agents_cli.commands.import_harbor import _read_json

        assert _read_json(f) == {"key": "value"}

    def test_missing_file_returns_empty(self, tmp_path):
        from nemo_oo_agents_cli.commands.import_harbor import _read_json

        assert _read_json(tmp_path / "missing.json") == {}

    def test_invalid_json_returns_empty(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not-json")

        from nemo_oo_agents_cli.commands.import_harbor import _read_json

        assert _read_json(f) == {}


class TestTrialMeta:
    def test_extracts_trial_meta(self, tmp_path):
        job_dir, trace_file = _make_harbor_job(
            tmp_path, trial_name="my-trial", score=0.75, task="algebra"
        )

        from nemo_oo_agents_cli.commands.import_harbor import _trial_meta

        meta = _trial_meta(trace_file)
        assert meta["trial_name"] == "my-trial"
        assert meta["task_name"] == "algebra"
        assert meta["agent_name"] == "TestAgent"
        assert meta["score"] == 0.75
        assert meta["experiment"] == "MyEval__train"
        assert meta["job_name"] == "my-job"

    def test_missing_result_uses_dir_name(self, tmp_path):
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        trial_dir = job_dir / "trial-99"
        trial_dir.mkdir()
        artifacts = trial_dir / "artifacts"
        artifacts.mkdir()
        trace_file = artifacts / "run.jsonl"
        trace_file.write_text(_OTLP_TRACE)

        from nemo_oo_agents_cli.commands.import_harbor import _trial_meta

        meta = _trial_meta(trace_file)
        assert meta["trial_name"] == "trial-99"
        assert meta["experiment"] == "harbor"  # fallback

    def test_reward_txt_fallback(self, tmp_path):
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        trial_dir = job_dir / "trial-txt"
        trial_dir.mkdir()
        verifier_dir = trial_dir / "verifier"
        verifier_dir.mkdir()
        (verifier_dir / "reward.txt").write_text("0.5\n")
        artifacts = trial_dir / "artifacts"
        artifacts.mkdir()
        trace_file = artifacts / "run.jsonl"
        trace_file.write_text(_OTLP_TRACE)

        from nemo_oo_agents_cli.commands.import_harbor import _trial_meta

        meta = _trial_meta(trace_file)
        assert meta["score"] == 0.5

    def test_invalid_reward_txt_score_is_none(self, tmp_path):
        job_dir = tmp_path / "job"
        job_dir.mkdir()
        trial_dir = job_dir / "trial-bad"
        trial_dir.mkdir()
        verifier_dir = trial_dir / "verifier"
        verifier_dir.mkdir()
        (verifier_dir / "reward.txt").write_text("not-a-float\n")
        artifacts = trial_dir / "artifacts"
        artifacts.mkdir()
        trace_file = artifacts / "run.jsonl"
        trace_file.write_text(_OTLP_TRACE)

        from nemo_oo_agents_cli.commands.import_harbor import _trial_meta

        meta = _trial_meta(trace_file)
        assert meta["score"] is None


class TestImportHarborCommand:
    def _get_command(self):
        from nemo_oo_agents_cli.commands.import_harbor import command

        return command

    def test_no_files_exits_1(self, tmp_path):
        empty = tmp_path / "nojob"
        empty.mkdir()

        runner = CliRunner()
        with _mock_harbor_helpers():
            result = runner.invoke(self._get_command(), [str(empty)])
        assert result.exit_code == 1
        assert "No Harbor trace files" in result.output

    def test_endpoint_unreachable_exits_1(self, tmp_path):
        job_dir, _ = _make_harbor_job(tmp_path)

        runner = CliRunner()
        with _mock_harbor_helpers(endpoint_reachable=False):
            result = runner.invoke(self._get_command(), [str(job_dir)])
        assert result.exit_code == 1
        assert "Cannot reach" in result.output

    def test_successful_import(self, tmp_path):
        job_dir, _ = _make_harbor_job(tmp_path, trial_name="trial-ok", score=1.0)

        runner = CliRunner()
        with _mock_harbor_helpers():
            result = runner.invoke(self._get_command(), [str(job_dir)])
        assert result.exit_code == 0
        assert "1 imported" in result.output

    def test_session_already_exists(self, tmp_path):
        job_dir, _ = _make_harbor_job(tmp_path)

        runner = CliRunner()
        with _mock_harbor_helpers(session_exists_val=True):
            result = runner.invoke(self._get_command(), [str(job_dir)])
        assert result.exit_code == 0
        assert "already exists" in result.output

    def test_custom_experiment_and_batch_id(self, tmp_path):
        job_dir, _ = _make_harbor_job(tmp_path)

        runner = CliRunner()
        with _mock_harbor_helpers():
            result = runner.invoke(
                self._get_command(),
                [str(job_dir), "--experiment", "my-exp", "--batch-id", "batch-99"],
            )
        assert result.exit_code == 0
        assert "batch-99" in result.output

    def test_post_trace_failure(self, tmp_path):
        job_dir, _ = _make_harbor_job(tmp_path)

        runner = CliRunner()
        with _mock_harbor_helpers(post_trace_val=False):
            result = runner.invoke(self._get_command(), [str(job_dir)])
        assert result.exit_code == 0
        assert "0 imported" in result.output or "failed" in result.output

    def test_score_displayed(self, tmp_path):
        job_dir, _ = _make_harbor_job(tmp_path, trial_name="trial-score", score=0.5)

        runner = CliRunner()
        with _mock_harbor_helpers():
            result = runner.invoke(self._get_command(), [str(job_dir)])
        assert result.exit_code == 0
        assert "0.500" in result.output

    def test_view_url_shown_on_success(self, tmp_path):
        job_dir, _ = _make_harbor_job(tmp_path)

        runner = CliRunner()
        with _mock_harbor_helpers():
            result = runner.invoke(self._get_command(), [str(job_dir)])
        assert result.exit_code == 0
        assert "View at:" in result.output

    def test_non_otlp_lines_skipped(self, tmp_path):
        job_dir = tmp_path / "my-job"
        job_dir.mkdir()
        trial_dir = job_dir / "trial-skip"
        trial_dir.mkdir()
        artifacts = trial_dir / "artifacts" / "traces"
        artifacts.mkdir(parents=True)
        # Only annotation-style lines, no resourceSpans
        trace_file = artifacts / "run.jsonl"
        trace_file.write_text(json.dumps({"no_resource_spans": True}))

        runner = CliRunner()
        with _mock_harbor_helpers():
            result = runner.invoke(self._get_command(), [str(job_dir)])
        assert result.exit_code == 0
        assert "0 imported" in result.output or "skipped" in result.output

    def test_invalid_json_in_trace_skipped(self, tmp_path):
        job_dir, trace_file = _make_harbor_job(tmp_path)
        trace_file.write_text("bad-json\n" + _OTLP_TRACE)

        runner = CliRunner()
        with _mock_harbor_helpers():
            result = runner.invoke(self._get_command(), [str(job_dir)])
        assert result.exit_code == 0

    def test_empty_lines_in_trace_skipped(self, tmp_path):
        job_dir, trace_file = _make_harbor_job(tmp_path)
        # Prepend empty lines to trigger the `continue` on line 196
        trace_file.write_text("\n\n" + _OTLP_TRACE + "\n\n")

        runner = CliRunner()
        with _mock_harbor_helpers():
            result = runner.invoke(self._get_command(), [str(job_dir)])
        assert result.exit_code == 0
        assert "1 imported" in result.output

    def test_score_none_shown_as_na(self, tmp_path):
        job_dir = tmp_path / "my-job-noscr"
        job_dir.mkdir()
        trial_dir = job_dir / "trial-noscr"
        trial_dir.mkdir()
        artifacts = trial_dir / "artifacts" / "traces"
        artifacts.mkdir(parents=True)
        (artifacts / "run.jsonl").write_text(_OTLP_TRACE)

        runner = CliRunner()
        with _mock_harbor_helpers():
            result = runner.invoke(self._get_command(), [str(job_dir)])
        assert result.exit_code == 0
        assert "n/a" in result.output
