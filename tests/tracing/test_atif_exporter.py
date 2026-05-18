# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the ATIF v1.7 trajectory builder and SpanExporter.

The builder is pure-Python -- we exercise it via synthetic ``SpanRecord``
fixtures.  The exporter is exercised with ``MagicMock`` stand-ins for
``ReadableSpan`` (the exporter only reads ``start_time`` / ``end_time`` /
``attributes`` / ``name``).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.trace.export import SpanExportResult

from nemo_oo_agents.tracing import (
    _LOCAL_EXPORTER_TYPES,
    AtifTrajectoryExporter,
    exporters,
)
from nemo_oo_agents.tracing._atif_exporter import (
    SCHEMA_VERSION,
    SpanRecord,
    build_trajectory_from_records,
)

# ---------------------------------------------------------------------------
# Synthetic span helpers
# ---------------------------------------------------------------------------


def _llm_record(
    *,
    start_ns: int,
    end_ns: int,
    input_messages: list[dict],
    output_messages: list[dict],
    model_name: str = "gpt-test",
    prompt_tokens: int = 100,
    completion_tokens: int = 20,
    cached_tokens: int = 0,
    cost: float = 0.001,
) -> SpanRecord:
    attrs: dict = {
        "openinference.span.kind": "LLM",
        "llm.model_name": model_name,
        "llm.token_count.prompt": prompt_tokens,
        "llm.token_count.completion": completion_tokens,
        "llm.token_count.prompt_details.cache_read": cached_tokens,
        "llm.cost.total": cost,
    }
    _stamp_messages(attrs, "llm.input_messages", input_messages)
    _stamp_messages(attrs, "llm.output_messages", output_messages)
    return SpanRecord(start_ns=start_ns, end_ns=end_ns, attrs=attrs, name="llm")


def _stamp_messages(attrs: dict, prefix: str, msgs: list[dict]) -> None:
    """Encode messages into the flat OTLP-attribute shape the builder reads."""
    for i, m in enumerate(msgs):
        base = f"{prefix}.{i}.message"
        if "role" in m:
            attrs[f"{base}.role"] = m["role"]
        if "content" in m:
            attrs[f"{base}.content"] = m["content"]
        if "tool_call_id" in m:
            attrs[f"{base}.tool_call_id"] = m["tool_call_id"]
        for j, tc in enumerate(m.get("tool_calls", [])):
            tc_base = f"{base}.tool_calls.{j}.tool_call"
            attrs[f"{tc_base}.id"] = tc.get("tool_call_id", "")
            attrs[f"{tc_base}.function.name"] = tc.get("function_name", "")
            args = tc.get("arguments", {})
            attrs[f"{tc_base}.function.arguments"] = (
                args if isinstance(args, str) else json.dumps(args)
            )


def _tool_record(
    *,
    start_ns: int,
    end_ns: int,
    tool_name: str = "python_executor",
    tool_call_id: str = "",
    result_type: str = "PythonOutput",
) -> SpanRecord:
    attrs = {
        "openinference.span.kind": "TOOL",
        "tool.name": tool_name,
        "tool_call_id": tool_call_id,
        "result.type": result_type,
    }
    return SpanRecord(start_ns=start_ns, end_ns=end_ns, attrs=attrs, name=tool_name)


def _readable_span_mock(rec: SpanRecord) -> MagicMock:
    """Wrap a SpanRecord in a MagicMock that quacks like a ReadableSpan."""
    m = MagicMock()
    m.start_time = rec.start_ns
    m.end_time = rec.end_ns
    m.attributes = rec.attrs
    m.name = rec.name
    return m


# ---------------------------------------------------------------------------
# Factory and class wiring
# ---------------------------------------------------------------------------


class TestAtifFactory:
    def test_factory_returns_span_exporter(self, tmp_path: Path) -> None:
        exp = exporters.atif(
            tmp_path / "trajectory.json",
            session_id="s1",
            agent_name="agent",
            agent_version="1.2.3",
        )
        assert isinstance(exp, AtifTrajectoryExporter)
        assert exp.session_id == "s1"
        assert exp.agent_name == "agent"
        assert exp.agent_version == "1.2.3"
        assert exp.path == tmp_path / "trajectory.json"

    def test_atif_exporter_in_local_exporter_types(self) -> None:
        # Regression guard: AtifTrajectoryExporter must use SimpleSpanProcessor
        # so the "partial trajectory on every export" guarantee holds.
        assert AtifTrajectoryExporter in _LOCAL_EXPORTER_TYPES


# ---------------------------------------------------------------------------
# Builder behaviour
# ---------------------------------------------------------------------------


class TestAtifBuilder:
    def test_minimal_trajectory(self) -> None:
        records = [
            _llm_record(
                start_ns=1_000_000_000,
                end_ns=2_000_000_000,
                input_messages=[
                    {"role": "system", "content": "You are an agent."},
                    {"role": "user", "content": "do the thing"},
                ],
                output_messages=[
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "tool_call_id": "call_abc",
                                "function_name": "shell",
                                "arguments": {"cmd": "ls"},
                            }
                        ],
                    }
                ],
                prompt_tokens=100,
                completion_tokens=20,
                cached_tokens=50,
                cost=0.0042,
            ),
            _tool_record(
                start_ns=2_100_000_000,
                end_ns=2_200_000_000,
                tool_call_id="call_abc",
                result_type="ShellOutput",
            ),
        ]

        traj = build_trajectory_from_records(
            records,
            session_id="trial-123",
            agent_name="my-agent",
            agent_version="0.1.0",
        )

        assert traj["schema_version"] == SCHEMA_VERSION
        assert traj["session_id"] == "trial-123"
        assert traj["agent"]["name"] == "my-agent"
        assert traj["agent"]["version"] == "0.1.0"
        assert traj["agent"]["model_name"] == "gpt-test"
        sources = [s["source"] for s in traj["steps"]]
        assert sources == ["system", "user", "agent"]
        agent_step = traj["steps"][-1]
        assert agent_step["tool_calls"] == [
            {
                "tool_call_id": "call_abc",
                "function_name": "shell",
                "arguments": {"cmd": "ls"},
            }
        ]
        fm = traj["final_metrics"]
        assert fm["total_prompt_tokens"] == 100
        assert fm["total_completion_tokens"] == 20
        assert fm["total_cached_tokens"] == 50
        assert fm["total_steps"] == len(traj["steps"])
        assert fm["extra"]["total_cost_usd"] == pytest.approx(0.0042)

    def test_drops_summarizer_calls(self) -> None:
        records = [
            _llm_record(
                start_ns=1_000_000_000,
                end_ns=2_000_000_000,
                input_messages=[
                    {"role": "system", "content": "TokenBudgetSummarizer internal"},
                    {"role": "user", "content": "summarize"},
                ],
                output_messages=[{"role": "assistant", "content": "summary"}],
                prompt_tokens=999,
                completion_tokens=11,
            ),
            _llm_record(
                start_ns=3_000_000_000,
                end_ns=4_000_000_000,
                input_messages=[
                    {"role": "system", "content": "You are a normal agent."},
                    {"role": "user", "content": "main task"},
                ],
                output_messages=[{"role": "assistant", "content": "done"}],
                prompt_tokens=50,
                completion_tokens=5,
            ),
        ]
        traj = build_trajectory_from_records(
            records,
            session_id="trial",
            agent_name="a",
            agent_version="0",
        )
        # Summarizer call must not contribute to step count or metrics.
        assert traj["final_metrics"]["total_prompt_tokens"] == 50
        assert traj["final_metrics"]["total_completion_tokens"] == 5
        # The main-agent's system prompt is the one we keep.
        first_system = next(s for s in traj["steps"] if s["source"] == "system")
        assert "TokenBudgetSummarizer" not in first_system["message"]

    def test_pairs_verbose_tool_followup_single_quote(self) -> None:
        records = [
            _llm_record(
                start_ns=1_000_000_000,
                end_ns=2_000_000_000,
                input_messages=[
                    {"role": "system", "content": "main"},
                    {"role": "user", "content": "go"},
                ],
                output_messages=[
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "tool_call_id": "call_xyz",
                                "function_name": "py",
                                "arguments": {"code": "print(1)"},
                            }
                        ],
                    }
                ],
            ),
            _llm_record(
                start_ns=3_000_000_000,
                end_ns=4_000_000_000,
                input_messages=[
                    {"role": "system", "content": "main"},
                    {"role": "user", "content": "go"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "tool_call_id": "call_xyz",
                                "function_name": "py",
                                "arguments": {"code": "print(1)"},
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_xyz",
                        "content": "status: complete",
                    },
                    {
                        "role": "user",
                        "content": (
                            "<sys tag=\"0\">PythonOutput(tool_call_id='call_xyz', "
                            "stdout='1\\n')</sys>"
                        ),
                    },
                ],
                output_messages=[{"role": "assistant", "content": "done"}],
            ),
        ]
        traj = build_trajectory_from_records(
            records, session_id="s", agent_name="a", agent_version="0"
        )
        agent_step = next(s for s in traj["steps"] if s.get("tool_calls"))
        results = agent_step["observation"]["results"]
        assert len(results) == 1
        assert results[0]["source_call_id"] == "call_xyz"
        assert "PythonOutput" in results[0]["content"]

    def test_pairs_verbose_tool_followup_double_quote(self) -> None:
        # Same as the single-quote test but with double quotes inside the body.
        # Pins both regex branches of tcid_in_body_re.
        records = [
            _llm_record(
                start_ns=1_000_000_000,
                end_ns=2_000_000_000,
                input_messages=[
                    {"role": "system", "content": "m"},
                    {"role": "user", "content": "g"},
                ],
                output_messages=[
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "tool_call_id": "call_zz",
                                "function_name": "py",
                                "arguments": {},
                            }
                        ],
                    }
                ],
            ),
            _llm_record(
                start_ns=3_000_000_000,
                end_ns=4_000_000_000,
                input_messages=[
                    {"role": "system", "content": "m"},
                    {"role": "user", "content": "g"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "tool_call_id": "call_zz",
                                "function_name": "py",
                                "arguments": {},
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": 'raw output for tool_call_id="call_zz" here',
                    },
                ],
                output_messages=[{"role": "assistant", "content": "ok"}],
            ),
        ]
        traj = build_trajectory_from_records(
            records, session_id="s", agent_name="a", agent_version="0"
        )
        agent_step = next(s for s in traj["steps"] if s.get("tool_calls"))
        results = agent_step["observation"]["results"]
        assert len(results) == 1
        assert results[0]["source_call_id"] == "call_zz"
        assert "call_zz" in results[0]["content"]

    def test_strips_trailing_context_block(self) -> None:
        body = "real instructions\n<context>noise</context>"
        records = [
            _llm_record(
                start_ns=1_000_000_000,
                end_ns=2_000_000_000,
                input_messages=[
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": body},
                ],
                output_messages=[{"role": "assistant", "content": "ack"}],
            )
        ]
        traj = build_trajectory_from_records(
            records, session_id="s", agent_name="a", agent_version="0"
        )
        user_step = next(s for s in traj["steps"] if s["source"] == "user")
        assert "<context>" not in user_step["message"]
        assert "real instructions" in user_step["message"]

    def test_empty_when_no_main_calls(self) -> None:
        records = [
            _llm_record(
                start_ns=1_000_000_000,
                end_ns=2_000_000_000,
                input_messages=[
                    {"role": "system", "content": "TokenBudgetSummarizer"},
                    {"role": "user", "content": "x"},
                ],
                output_messages=[{"role": "assistant", "content": "y"}],
            )
        ]
        traj = build_trajectory_from_records(
            records, session_id="s", agent_name="a", agent_version="0"
        )
        assert traj["steps"] == []
        assert traj["final_metrics"] == {"total_steps": 0}

    def test_empty_trajectory_preserves_agent_extra(self) -> None:
        # The empty-trajectory branch must include caller-supplied
        # agent_extra; otherwise the emitted metadata would depend on
        # timing (whether any main LLM span had been observed yet).
        traj = build_trajectory_from_records(
            [],
            session_id="s",
            agent_name="a",
            agent_version="0",
            agent_extra={"cwd": "/workspace"},
        )
        assert traj["steps"] == []
        assert traj["agent"]["extra"] == {"cwd": "/workspace"}

    def test_keeps_llm_spans_with_zero_prompt_tokens(self) -> None:
        # Providers that don't instrument token counts (or partial OTLP
        # envelopes) leave llm.token_count.prompt absent/zero.  Such spans
        # still carry input_messages / output_messages and must contribute
        # to the trajectory; otherwise an uninstrumented run produces a
        # silently empty trajectory.
        rec = _llm_record(
            start_ns=1_000_000_000,
            end_ns=2_000_000_000,
            input_messages=[
                {"role": "system", "content": "s"},
                {"role": "user", "content": "u"},
            ],
            output_messages=[{"role": "assistant", "content": "a"}],
            prompt_tokens=0,
            completion_tokens=0,
        )
        rec.attrs.pop("llm.token_count.prompt", None)
        traj = build_trajectory_from_records(
            [rec], session_id="s", agent_name="a", agent_version="0"
        )
        sources = [s["source"] for s in traj["steps"]]
        assert sources == ["system", "user", "agent"]

    def test_keeps_repeated_user_turns(self) -> None:
        # Two main-agent LLM calls; the second snapshot's input_messages
        # repeats the same user content verbatim at a new conversation
        # position (e.g. user re-issues the same instruction).  Content-
        # based dedup would drop the second occurrence; prefix-based union
        # keeps it.
        call1 = _llm_record(
            start_ns=1_000_000_000,
            end_ns=2_000_000_000,
            input_messages=[
                {"role": "system", "content": "s"},
                {"role": "user", "content": "do it"},
            ],
            output_messages=[{"role": "assistant", "content": "done"}],
        )
        call2 = _llm_record(
            start_ns=3_000_000_000,
            end_ns=4_000_000_000,
            input_messages=[
                {"role": "system", "content": "s"},
                {"role": "user", "content": "do it"},
                {"role": "assistant", "content": "done"},
                {"role": "user", "content": "do it"},  # repeated
            ],
            output_messages=[{"role": "assistant", "content": "done again"}],
        )
        traj = build_trajectory_from_records(
            [call1, call2], session_id="s", agent_name="a", agent_version="0"
        )
        user_steps = [s for s in traj["steps"] if s["source"] == "user"]
        assert len(user_steps) == 2
        assert all(s["message"] == "do it" for s in user_steps)


# ---------------------------------------------------------------------------
# Exporter behaviour
# ---------------------------------------------------------------------------


class TestAtifExporter:
    def _basic_span_seq(self) -> list[SpanRecord]:
        return [
            _llm_record(
                start_ns=1_000_000_000,
                end_ns=2_000_000_000,
                input_messages=[
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "u"},
                ],
                output_messages=[{"role": "assistant", "content": "a"}],
            )
        ]

    def test_writes_atomic_trajectory(self, tmp_path: Path) -> None:
        out = tmp_path / "nested" / "trajectory.json"
        exp = AtifTrajectoryExporter(
            out,
            session_id="s",
            agent_name="a",
            agent_version="0",
        )
        spans = [_readable_span_mock(r) for r in self._basic_span_seq()]
        result = exp.export(spans)
        assert result == SpanExportResult.SUCCESS
        assert out.exists()
        # The parent directory was created on demand.
        assert out.parent.exists()
        # No lingering .tmp file after a successful atomic rename.
        assert list(out.parent.glob("*.tmp")) == []
        # And the on-disk file is well-formed JSON with the expected top keys.
        loaded = json.loads(out.read_text())
        assert loaded["schema_version"] == SCHEMA_VERSION
        assert loaded["session_id"] == "s"

    def test_shutdown_idempotent(self, tmp_path: Path) -> None:
        out = tmp_path / "traj.json"
        exp = AtifTrajectoryExporter(out, session_id="s", agent_name="a", agent_version="0")
        exp.export([_readable_span_mock(r) for r in self._basic_span_seq()])
        exp.shutdown()
        # Second shutdown must not raise.
        exp.shutdown()

    def test_force_flush_writes_when_called(self, tmp_path: Path) -> None:
        out = tmp_path / "traj.json"
        exp = AtifTrajectoryExporter(
            out,
            session_id="s",
            agent_name="a",
            agent_version="0",
            write_on_each_export=False,
        )
        exp.export([_readable_span_mock(r) for r in self._basic_span_seq()])
        # write_on_each_export=False -- nothing on disk yet.
        assert not out.exists()
        assert exp.force_flush() is True
        assert out.exists()

    def test_export_after_shutdown_returns_failure(self, tmp_path: Path) -> None:
        out = tmp_path / "traj.json"
        exp = AtifTrajectoryExporter(out, session_id="s", agent_name="a", agent_version="0")
        exp.shutdown()
        result = exp.export([_readable_span_mock(r) for r in self._basic_span_seq()])
        assert result == SpanExportResult.FAILURE

    def test_force_flush_after_shutdown_returns_false(self, tmp_path: Path) -> None:
        # Guard against overwriting the on-disk trajectory with one built
        # from a buffer that shutdown() already cleared.  shutdown() does its
        # own final _write_atomic(), so we snapshot mtime *after* shutdown,
        # then assert force_flush is a no-op against that committed state.
        out = tmp_path / "traj.json"
        exp = AtifTrajectoryExporter(out, session_id="s", agent_name="a", agent_version="0")
        exp.export([_readable_span_mock(r) for r in self._basic_span_seq()])
        exp.shutdown()
        committed_mtime_ns = out.stat().st_mtime_ns
        assert exp.force_flush() is False
        # The committed trajectory was not modified by the post-shutdown flush.
        assert out.stat().st_mtime_ns == committed_mtime_ns

    def test_write_on_each_export_false_defers(self, tmp_path: Path) -> None:
        out = tmp_path / "traj.json"
        exp = AtifTrajectoryExporter(
            out,
            session_id="s",
            agent_name="a",
            agent_version="0",
            write_on_each_export=False,
        )
        exp.export([_readable_span_mock(r) for r in self._basic_span_seq()])
        assert not out.exists()
        exp.shutdown()
        assert out.exists()

    def test_model_name_kwarg_fallback_when_no_inferable_name(self, tmp_path: Path) -> None:
        # An LLM record without an llm.model_name attribute.  Use pop() so the
        # key is absent, not just blank — the production code only checks
        # truthiness, but a missing-key bug would slip through if we asserted
        # only the empty-string case.
        rec = _llm_record(
            start_ns=1_000_000_000,
            end_ns=2_000_000_000,
            input_messages=[
                {"role": "system", "content": "s"},
                {"role": "user", "content": "u"},
            ],
            output_messages=[{"role": "assistant", "content": "a"}],
            model_name="",
        )
        rec.attrs.pop("llm.model_name", None)
        out = tmp_path / "traj.json"
        exp = AtifTrajectoryExporter(
            out,
            session_id="s",
            agent_name="a",
            agent_version="0",
            model_name="azure/openai/gpt-5.5",
        )
        exp.export([_readable_span_mock(rec)])
        traj = json.loads(out.read_text())
        assert traj["agent"]["model_name"] == "azure/openai/gpt-5.5"

    def test_model_name_kwarg_does_not_override_inferred(self, tmp_path: Path) -> None:
        # When the builder CAN infer model_name, the kwarg must not overwrite it.
        rec = _llm_record(
            start_ns=1_000_000_000,
            end_ns=2_000_000_000,
            input_messages=[
                {"role": "system", "content": "s"},
                {"role": "user", "content": "u"},
            ],
            output_messages=[{"role": "assistant", "content": "a"}],
            model_name="gpt-real",
        )
        out = tmp_path / "traj.json"
        exp = AtifTrajectoryExporter(
            out,
            session_id="s",
            agent_name="a",
            agent_version="0",
            model_name="should-not-win",
        )
        exp.export([_readable_span_mock(rec)])
        traj = json.loads(out.read_text())
        assert traj["agent"]["model_name"] == "gpt-real"

    def test_end_to_end_through_enable_tracing(self, tmp_path: Path) -> None:
        """Smoke test exercising the processor-selection wiring.

        The AtifTrajectoryExporter must be registered with SimpleSpanProcessor
        (because it is in _LOCAL_EXPORTER_TYPES); we verify that an exporter
        instance plumbed into enable_tracing actually writes its trajectory
        file when a span is emitted.
        """
        from opentelemetry import trace

        from nemo_oo_agents.tracing import (
            enable_tracing,
            flush_traces,
            shutdown_traces,
        )

        target = tmp_path / "trajectory.json"
        exp = exporters.atif(
            target,
            session_id="e2e-session",
            agent_name="agent",
            agent_version="0.0.0",
        )
        enable_tracing(exporters=[exp])
        try:
            tracer = trace.get_tracer(__name__)
            # Emit a span with the openinference LLM attributes the builder
            # expects, so we exercise the full span -> step rendering path
            # (not just the noop file-creation behaviour).
            with tracer.start_as_current_span(
                "llm_span",
                attributes={
                    "openinference.span.kind": "LLM",
                    "llm.model_name": "gpt-test",
                    "llm.input_messages.0.message.role": "system",
                    "llm.input_messages.0.message.content": "s",
                    "llm.input_messages.1.message.role": "user",
                    "llm.input_messages.1.message.content": "u",
                    "llm.output_messages.0.message.role": "assistant",
                    "llm.output_messages.0.message.content": "a",
                },
            ):
                pass
            # Assert before flush: SimpleSpanProcessor must have invoked
            # export() synchronously when the span ended.  A BatchSpanProcessor
            # wiring regression would buffer the span and the file would not
            # exist yet.
            assert target.exists()
            loaded = json.loads(target.read_text())
            assert loaded["session_id"] == "e2e-session"
            sources = [s["source"] for s in loaded["steps"]]
            assert sources == ["system", "user", "agent"]
            flush_traces()
        finally:
            shutdown_traces()
