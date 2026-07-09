# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
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

        from nooa_cli.commands.import_traces import _find_trace_files

        result = _find_trace_files(f)
        assert result == [f]

    def test_directory_finds_jsonl(self, tmp_path):
        f = tmp_path / "run.jsonl"
        f.write_text(_OTLP_TRACE)

        from nooa_cli.commands.import_traces import _find_trace_files

        result = _find_trace_files(tmp_path)
        assert f in result

    def test_nonexistent_path_returns_empty(self, tmp_path):
        from nooa_cli.commands.import_traces import _find_trace_files

        result = _find_trace_files(tmp_path / "does_not_exist")
        assert result == []

    def test_nested_files_found(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        f = sub / "trace.jsonl"
        f.write_text(_OTLP_TRACE)

        from nooa_cli.commands.import_traces import _find_trace_files

        result = _find_trace_files(tmp_path)
        assert f in result

    def test_no_duplicates(self, tmp_path):
        f = tmp_path / "run.jsonl"
        f.write_text(_OTLP_TRACE)

        from nooa_cli.commands.import_traces import _find_trace_files

        result = _find_trace_files(tmp_path)
        assert len(result) == len({r.resolve() for r in result})


class TestDetectFormat:
    def test_otlp(self):
        from nooa_cli.commands.import_traces import _detect_format

        assert _detect_format({"resourceSpans": []}) == "otlp"

    def test_legacy_span_id(self):
        from nooa_cli.commands.import_traces import _detect_format

        assert _detect_format({"span_id": "abc"}) == "legacy"

    def test_legacy_trace_id(self):
        from nooa_cli.commands.import_traces import _detect_format

        assert _detect_format({"trace_id": "xyz"}) == "legacy"

    def test_unknown(self):
        from nooa_cli.commands.import_traces import _detect_format

        assert _detect_format({"some_key": "value"}) == "unknown"


class TestSessionIdFromFilename:
    def test_strips_jsonl(self):
        from nooa_cli.commands.import_traces import _session_id_from_filename

        p = Path("/some/dir/my_trace.jsonl")
        assert _session_id_from_filename(p) == "my_trace"

    def test_no_known_extension(self):
        from nooa_cli.commands.import_traces import _session_id_from_filename

        p = Path("/some/dir/my_trace.txt")
        assert _session_id_from_filename(p) == "my_trace"


# ---------------------------------------------------------------------------
# import_traces CLI command tests
# ---------------------------------------------------------------------------

_HELPERS_PATH = "nooa_cli.commands.import_traces"


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
        from nooa_cli.commands.import_traces import command

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

_HARBOR_HELPERS_PATH = "nooa_cli.commands.import_harbor"


def _mock_harbor_helpers(
    endpoint_reachable=True,
    session_exists_val=False,
    post_traces_batch_val=True,
    post_traces_batch_mock=None,
):
    return patch.multiple(
        _HARBOR_HELPERS_PATH,
        validate_endpoint=MagicMock(),
        check_endpoint_reachable=MagicMock(return_value=endpoint_reachable),
        session_exists=MagicMock(return_value=session_exists_val),
        post_traces_batch=(
            post_traces_batch_mock
            if post_traces_batch_mock is not None
            else MagicMock(return_value=post_traces_batch_val)
        ),
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

        from nooa_cli.commands.import_harbor import _find_harbor_traces

        result = _find_harbor_traces(job_dir)
        assert trace_file in result

    def test_empty_dir_returns_empty(self, tmp_path):
        from nooa_cli.commands.import_harbor import _find_harbor_traces

        result = _find_harbor_traces(tmp_path)
        assert result == []


class TestReadJson:
    def test_reads_valid_json(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text('{"key": "value"}')

        from nooa_cli.commands.import_harbor import _read_json

        assert _read_json(f) == {"key": "value"}

    def test_missing_file_returns_empty(self, tmp_path):
        from nooa_cli.commands.import_harbor import _read_json

        assert _read_json(tmp_path / "missing.json") == {}

    def test_invalid_json_returns_empty(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not-json")

        from nooa_cli.commands.import_harbor import _read_json

        assert _read_json(f) == {}


class TestTrialMeta:
    def test_extracts_trial_meta(self, tmp_path):
        job_dir, trace_file = _make_harbor_job(
            tmp_path, trial_name="my-trial", score=0.75, task="algebra"
        )

        from nooa_cli.commands.import_harbor import _trial_meta

        meta = _trial_meta(trace_file)
        assert meta["trial_name"] == "my-trial"
        assert meta["task_name"] == "algebra"
        assert meta["agent_name"] == "TestAgent"
        assert meta["score"] == 0.75
        assert meta["harbor_eval"] == "MyEval__train"
        assert meta["experiment"] == "my-job"
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

        from nooa_cli.commands.import_harbor import _trial_meta

        meta = _trial_meta(trace_file)
        assert meta["trial_name"] == "trial-99"
        assert meta["experiment"] == "job"

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

        from nooa_cli.commands.import_harbor import _trial_meta

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

        from nooa_cli.commands.import_harbor import _trial_meta

        meta = _trial_meta(trace_file)
        assert meta["score"] is None


class TestReadScore:
    """Score fallback across current Harbor result shapes (issue #224)."""

    def _trial(self, tmp_path):
        trial_dir = tmp_path / "trial"
        (trial_dir / "verifier").mkdir(parents=True)
        return trial_dir

    def test_reward_json_score_key(self, tmp_path):
        from nooa_cli.commands.import_harbor import _read_score

        trial = self._trial(tmp_path)
        (trial / "verifier" / "reward.json").write_text(json.dumps({"score": 0.9}))
        assert _read_score(trial, {}) == 0.9

    def test_reward_json_reward_key(self, tmp_path):
        from nooa_cli.commands.import_harbor import _read_score

        trial = self._trial(tmp_path)
        (trial / "verifier" / "reward.json").write_text(json.dumps({"reward": 0.5}))
        assert _read_score(trial, {}) == 0.5

    def test_score_key_takes_precedence_over_reward(self, tmp_path):
        from nooa_cli.commands.import_harbor import _read_score

        trial = self._trial(tmp_path)
        (trial / "verifier" / "reward.json").write_text(json.dumps({"score": 1.0, "reward": 0.0}))
        assert _read_score(trial, {}) == 1.0

    def test_result_json_rewards_reward_fallback(self, tmp_path):
        from nooa_cli.commands.import_harbor import _read_score

        trial = self._trial(tmp_path)
        # No reward.json — fall through to result.json verifier_result.rewards
        trial_result = {"verifier_result": {"rewards": {"reward": 0.25}}}
        assert _read_score(trial, trial_result) == 0.25

    def test_result_json_rewards_score_preferred(self, tmp_path):
        from nooa_cli.commands.import_harbor import _read_score

        trial = self._trial(tmp_path)
        trial_result = {"verifier_result": {"rewards": {"score": 0.7, "reward": 0.1}}}
        assert _read_score(trial, trial_result) == 0.7

    def test_rewards_non_dict_does_not_crash(self, tmp_path):
        from nooa_cli.commands.import_harbor import _read_score

        trial = self._trial(tmp_path)
        # rewards as a list (unexpected shape) → skipped, falls through to None
        trial_result = {"verifier_result": {"rewards": [{"reward": 0.5}]}}
        assert _read_score(trial, trial_result) is None

    def test_zero_score_preserved(self, tmp_path):
        from nooa_cli.commands.import_harbor import _read_score

        trial = self._trial(tmp_path)
        (trial / "verifier" / "reward.json").write_text(json.dumps({"reward": 0.0}))
        assert _read_score(trial, {}) == 0.0

    def test_reward_txt_last_resort(self, tmp_path):
        from nooa_cli.commands.import_harbor import _read_score

        trial = self._trial(tmp_path)
        (trial / "verifier" / "reward.txt").write_text("0.42\n")
        assert _read_score(trial, {}) == 0.42

    def test_nothing_found_returns_none(self, tmp_path):
        from nooa_cli.commands.import_harbor import _read_score

        trial = self._trial(tmp_path)
        assert _read_score(trial, {}) is None

    def test_string_reward_coerced(self, tmp_path):
        from nooa_cli.commands.import_harbor import _read_score

        trial = self._trial(tmp_path)
        (trial / "verifier" / "reward.json").write_text(json.dumps({"reward": "0.6"}))
        assert _read_score(trial, {}) == 0.6

    def test_overflowing_integer_reward_does_not_crash(self, tmp_path):
        from nooa_cli.commands.import_harbor import _read_score

        trial = self._trial(tmp_path)
        # An astronomically large integer overflows float(); must fall through.
        (trial / "verifier" / "reward.json").write_text(json.dumps({"reward": 10**400}))
        assert _read_score(trial, {}) is None


class TestImportHarborCommand:
    def _get_command(self):
        from nooa_cli.commands.import_harbor import command

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
        with _mock_harbor_helpers(post_traces_batch_val=False):
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

    def test_batching_reduces_post_count(self, tmp_path):
        # 5 OTLP lines in one file with --batch-lines 5 → a single batched POST.
        job_dir, trace_file = _make_harbor_job(tmp_path)
        trace_file.write_text("\n".join([_OTLP_TRACE] * 5))

        batch_mock = MagicMock(return_value=True)
        runner = CliRunner()
        with _mock_harbor_helpers(post_traces_batch_mock=batch_mock):
            result = runner.invoke(self._get_command(), [str(job_dir), "--batch-lines", "5"])
        assert result.exit_code == 0
        assert "1 imported" in result.output
        # One flush for the whole file, far fewer than 5 line-by-line posts.
        assert batch_mock.call_count == 1
        # The batch carried all 5 envelopes.
        bodies = batch_mock.call_args.args[1]
        assert len(bodies) == 5

    def test_batch_lines_one_flushes_per_line(self, tmp_path):
        job_dir, trace_file = _make_harbor_job(tmp_path)
        trace_file.write_text("\n".join([_OTLP_TRACE] * 3))

        batch_mock = MagicMock(return_value=True)
        runner = CliRunner()
        with _mock_harbor_helpers(post_traces_batch_mock=batch_mock):
            result = runner.invoke(self._get_command(), [str(job_dir), "--batch-lines", "1"])
        assert result.exit_code == 0
        assert batch_mock.call_count == 3

    def test_batch_bytes_triggers_flush(self, tmp_path):
        job_dir, trace_file = _make_harbor_job(tmp_path)
        trace_file.write_text("\n".join([_OTLP_TRACE] * 4))

        batch_mock = MagicMock(return_value=True)
        runner = CliRunner()
        # Tiny byte budget forces a flush after each line regardless of batch-lines.
        with _mock_harbor_helpers(post_traces_batch_mock=batch_mock):
            result = runner.invoke(
                self._get_command(),
                [str(job_dir), "--batch-lines", "100", "--batch-bytes", "1"],
            )
        assert result.exit_code == 0
        assert batch_mock.call_count == 4


# ---------------------------------------------------------------------------
# post_traces_batch unit tests
# ---------------------------------------------------------------------------


class TestPostTracesBatch:
    def test_merges_resource_spans(self):
        from nooa_cli.commands import _otlp_helpers

        bodies = [
            {"resourceSpans": [{"a": 1}]},
            {"resourceSpans": [{"b": 2}, {"c": 3}]},
        ]
        with patch.object(_otlp_helpers, "post_trace", return_value=True) as pt:
            assert _otlp_helpers.post_traces_batch("http://x", bodies) is True
        pt.assert_called_once()
        merged = pt.call_args.args[1]
        assert merged["resourceSpans"] == [{"a": 1}, {"b": 2}, {"c": 3}]

    def test_empty_input_no_post(self):
        from nooa_cli.commands import _otlp_helpers

        with patch.object(_otlp_helpers, "post_trace", return_value=True) as pt:
            assert _otlp_helpers.post_traces_batch("http://x", []) is True
        pt.assert_not_called()

    def test_bodies_without_spans_no_post(self):
        from nooa_cli.commands import _otlp_helpers

        with patch.object(_otlp_helpers, "post_trace", return_value=True) as pt:
            assert _otlp_helpers.post_traces_batch("http://x", [{}, {}]) is True
        pt.assert_not_called()

    def test_non_list_resource_spans_skipped(self):
        from nooa_cli.commands import _otlp_helpers

        # Malformed bodies: resourceSpans is None / a dict → skipped, not raised.
        bodies = [
            {"resourceSpans": None},
            {"resourceSpans": {"bad": "shape"}},
            {"resourceSpans": [{"ok": 1}]},
        ]
        with patch.object(_otlp_helpers, "post_trace", return_value=True) as pt:
            assert _otlp_helpers.post_traces_batch("http://x", bodies) is True
        pt.assert_called_once()
        assert pt.call_args.args[1]["resourceSpans"] == [{"ok": 1}]

    def test_propagates_failure(self):
        from nooa_cli.commands import _otlp_helpers

        with patch.object(_otlp_helpers, "post_trace", return_value=False):
            assert _otlp_helpers.post_traces_batch("http://x", [{"resourceSpans": [{}]}]) is False


class TestImportHarborEvalOnly:
    def _get_command(self):
        from nooa_cli.commands.import_harbor import command

        return command

    def test_eval_only_posts_harbor_results_without_trace_files(self, tmp_path):
        job_dir = tmp_path / "my-job"
        job_dir.mkdir()
        trial_dir = job_dir / "trial-001"
        trial_dir.mkdir()
        (trial_dir / "config.json").write_text(
            json.dumps(
                {
                    "agent": {
                        "name": "nemo-oo-agents",
                        "model_name": "gpt-5.5-reasoning-high",
                        "kwargs": {"agent_type": "bench"},
                    },
                    "task": {"source": "swebench_all"},
                }
            )
        )
        (trial_dir / "result.json").write_text(
            json.dumps(
                {
                    "trial_name": "trial-001",
                    "task_name": "django__django-12345",
                    "source": "swebench_all",
                    "verifier_result": {"rewards": {"reward": 1.0}},
                    "config": {
                        "agent": {
                            "name": "nemo-oo-agents",
                            "model_name": "gpt-5.5-reasoning-high",
                            "kwargs": {"agent_type": "bench"},
                        },
                        "task": {"source": "swebench_all"},
                    },
                }
            )
        )
        (job_dir / "result.json").write_text(
            json.dumps({"stats": {"evals": {"SWEbench__fixed": {"n_trials": 1}}}})
        )

        posted = []

        def capture_batch(endpoint, bodies):
            posted.extend(bodies)
            return True

        runner = CliRunner()
        with _mock_harbor_helpers(post_traces_batch_mock=MagicMock(side_effect=capture_batch)):
            result = runner.invoke(
                self._get_command(),
                [str(job_dir), "--endpoint", "http://viewer:5001", "--eval-only"],
            )

        assert result.exit_code == 0
        assert "1 imported" in result.output
        assert "Evaluations:" in result.output
        assert posted
        resource_attrs = posted[0]["resourceSpans"][0]["resource"]["attributes"]
        attrs = {a["key"]: next(iter(a["value"].values())) for a in resource_attrs}
        assert attrs["session.id"] == "trial-001"
        assert attrs["experiment"] == "my-job"
        assert attrs["batch_id"] == "my-job"
        assert attrs["eval.harbor_eval"] == "SWEbench__fixed"
        assert attrs["eval.test_id"] == "django__django-12345"
        assert attrs["eval.model"] == "gpt-5.5-reasoning-high"
        assert attrs["eval.agent_class"] == "bench"
        assert attrs["eval.passed"] is True

    def test_eval_only_supports_custom_experiment_and_batch_id(self, tmp_path):
        job_dir, _ = _make_harbor_job(tmp_path, trial_name="trial-x", score=0.0)
        # Remove trace file so this exercises metadata-only import.
        for f in job_dir.rglob("*.jsonl"):
            f.unlink()

        posted = []

        def capture_batch(endpoint, bodies):
            posted.extend(bodies)
            return True

        runner = CliRunner()
        with _mock_harbor_helpers(post_traces_batch_mock=MagicMock(side_effect=capture_batch)):
            result = runner.invoke(
                self._get_command(),
                [
                    str(job_dir),
                    "--eval-only",
                    "--experiment",
                    "custom-exp",
                    "--batch-id",
                    "batch-1",
                ],
            )

        assert result.exit_code == 0
        resource_attrs = posted[0]["resourceSpans"][0]["resource"]["attributes"]
        attrs = {a["key"]: next(iter(a["value"].values())) for a in resource_attrs}
        assert attrs["experiment"] == "custom-exp"
        assert attrs["batch_id"] == "batch-1"
        assert attrs["eval.passed"] is False

    def test_eval_only_uses_matching_live_session_when_viewer_finds_one(self, tmp_path):
        job_dir, _ = _make_harbor_job(
            tmp_path,
            trial_name="trial-001",
            score=1.0,
            task="django__django-12345",
        )
        for f in job_dir.rglob("*.jsonl"):
            f.unlink()

        posted = []

        def capture_batch(endpoint, bodies):
            posted.extend(bodies)
            return True

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"match": {"session_id": "live-session-123"}}).encode()

        runner = CliRunner()
        with _mock_harbor_helpers(post_traces_batch_mock=MagicMock(side_effect=capture_batch)):
            with patch(
                "nooa_cli.commands.import_harbor.urllib.request.urlopen",
                return_value=FakeResponse(),
            ) as urlopen:
                result = runner.invoke(
                    self._get_command(),
                    [str(job_dir), "--endpoint", "http://viewer:5001", "--eval-only"],
                )

        assert result.exit_code == 0
        assert "trial-001 -> live-session-123" in result.output
        assert urlopen.called
        url = urlopen.call_args.args[0].full_url
        assert "task_name=django__django-12345" in url
        assert "experiment=my-job" in url or "experiment=default" in url
        resource_attrs = posted[0]["resourceSpans"][0]["resource"]["attributes"]
        attrs = {a["key"]: next(iter(a["value"].values())) for a in resource_attrs}
        assert attrs["session.id"] == "live-session-123"
        assert attrs["eval.harbor_trial_name"] == "trial-001"
