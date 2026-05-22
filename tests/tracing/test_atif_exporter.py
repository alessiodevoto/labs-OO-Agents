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
    result: str = "",
) -> SpanRecord:
    attrs = {
        "openinference.span.kind": "TOOL",
        "tool.name": tool_name,
        "tool_call_id": tool_call_id,
        "result.type": result_type,
    }
    if result:
        attrs["result"] = result
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
        # ATIF v1.7 §FinalMetricsSchema places total_cost_usd at top level.
        assert fm["total_cost_usd"] == pytest.approx(0.0042)

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
# Issue #215 — ATIF v1.7 compliance regressions
# ---------------------------------------------------------------------------


class TestAtifV17Compliance:
    """Regression tests pinning the three RFC violations called out in #215
    plus the optional fields populated from data we already track.

    See ``.development/docs/design/issue-215-atif-v1.7-compliance.md``.
    """

    def _agent_step_with_tool_call(
        self, *, tool_call_id: str = "call_xyz", placeholder_only: bool = True
    ) -> list[SpanRecord]:
        """Two-call sequence where call 1 issues a tool_call and call 2
        contains the follow-up. ``placeholder_only=True`` means the only
        tool follow-up is the ``status: complete`` role=tool message — i.e.
        no verbose ``<sys tag=>PythonOutput</sys>`` user message — so the
        existing chat-message scan path has nothing real to attach.
        """
        followup_messages: list[dict] = [
            {"role": "system", "content": "main"},
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "tool_call_id": tool_call_id,
                        "function_name": "execute_python",
                        "arguments": {"code": "print('hi')"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": "status: complete",
            },
        ]
        if not placeholder_only:
            followup_messages.append(
                {
                    "role": "user",
                    "content": (
                        f"<sys tag=\"0\">PythonOutput(tool_call_id='{tool_call_id}', "
                        "stdout='hi\\n')</sys>"
                    ),
                }
            )
        return [
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
                                "tool_call_id": tool_call_id,
                                "function_name": "execute_python",
                                "arguments": {"code": "print('hi')"},
                            }
                        ],
                    }
                ],
            ),
            _llm_record(
                start_ns=3_000_000_000,
                end_ns=4_000_000_000,
                input_messages=followup_messages,
                output_messages=[{"role": "assistant", "content": "done"}],
            ),
        ]

    def test_observation_attached_from_tool_span_when_message_log_lacks_content(
        self,
    ) -> None:
        """F1 regression: the TOOL span's JSON-encoded result MUST be the
        primary source of observation content. The existing chat-message
        path only sees ``status: complete`` placeholders for execute_python
        flows, which is not a usable observation.
        """
        records = self._agent_step_with_tool_call(placeholder_only=True)
        records.append(
            _tool_record(
                start_ns=2_100_000_000,
                end_ns=2_200_000_000,
                tool_call_id="call_xyz",
                result=json.dumps(
                    {
                        "stdout": "hi\n",
                        "stderr": "",
                        "returned_value": None,
                    }
                ),
            )
        )
        traj = build_trajectory_from_records(
            records, session_id="s", agent_name="a", agent_version="0"
        )
        agent_step = next(s for s in traj["steps"] if s.get("tool_calls"))
        # Must have observation
        assert "observation" in agent_step, "F1: tool_calls step must have observation paired"
        results = agent_step["observation"]["results"]
        assert len(results) == 1
        assert results[0]["source_call_id"] == "call_xyz"
        # Must be the real stdout, not the role=tool placeholder
        content = results[0]["content"]
        assert content != "status: complete", (
            "F1: observation content must come from the TOOL span result, "
            "not the role=tool placeholder. Got the placeholder instead."
        )
        assert "hi" in content, (
            f"F1: TOOL span stdout 'hi' must appear in observation content; got {content!r}"
        )

    def test_observation_falls_back_to_message_log_when_no_tool_span(self) -> None:
        """F1 fallback: when no TOOL span exists (e.g. a tool dispatched
        outside the code_execution hook), we still attach the observation
        from the verbose user follow-up message.
        """
        records = self._agent_step_with_tool_call(placeholder_only=False)
        traj = build_trajectory_from_records(
            records, session_id="s", agent_name="a", agent_version="0"
        )
        agent_step = next(s for s in traj["steps"] if s.get("tool_calls"))
        assert "observation" in agent_step
        results = agent_step["observation"]["results"]
        assert results[0]["source_call_id"] == "call_xyz"
        assert "PythonOutput" in results[0]["content"]

    def test_agent_message_is_assistant_content_not_executed_log(self) -> None:
        """F2 regression: when the assistant emits only a tool_call (no
        natural-language text), ``step.message`` MUST be the empty string,
        not the synthetic ``"Executed execute_python <id>"`` log line.
        """
        records = [
            _llm_record(
                start_ns=1_000_000_000,
                end_ns=2_000_000_000,
                input_messages=[
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "u"},
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
            )
        ]
        traj = build_trajectory_from_records(
            records, session_id="s", agent_name="a", agent_version="0"
        )
        agent_step = next(s for s in traj["steps"] if s.get("tool_calls"))
        assert agent_step["message"] == "", (
            f"F2: agent message must be the assistant's text (empty here), "
            f"not a synthetic log line. Got {agent_step['message']!r}."
        )
        # tool_calls field still populated.
        assert agent_step["tool_calls"][0]["tool_call_id"] == "call_abc"

    def test_agent_message_preserves_real_assistant_text_when_present(self) -> None:
        """F2 sanity: real assistant text MUST still flow through to message
        (we are dropping the synthetic fallback, not the real value).
        """
        records = [
            _llm_record(
                start_ns=1_000_000_000,
                end_ns=2_000_000_000,
                input_messages=[
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "u"},
                ],
                output_messages=[
                    {
                        "role": "assistant",
                        "content": "Now I'll list the files.",
                        "tool_calls": [
                            {
                                "tool_call_id": "call_abc",
                                "function_name": "shell",
                                "arguments": {"cmd": "ls"},
                            }
                        ],
                    }
                ],
            )
        ]
        traj = build_trajectory_from_records(
            records, session_id="s", agent_name="a", agent_version="0"
        )
        agent_step = next(s for s in traj["steps"] if s.get("tool_calls"))
        assert agent_step["message"] == "Now I'll list the files."

    def test_empty_user_message_after_context_strip_is_dropped(self) -> None:
        """F3 regression: the CachedBlockFormatter appends a trailing USER
        message containing only a ``<context>...</context>`` envelope. After
        ``_strip_context_block`` removes the envelope the content is empty
        — this must NOT be emitted as a phantom user step.
        """
        records = [
            _llm_record(
                start_ns=1_000_000_000,
                end_ns=2_000_000_000,
                input_messages=[
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "real user prompt"},
                    {"role": "user", "content": "<context>\ndynamic\n</context>"},
                ],
                output_messages=[{"role": "assistant", "content": "ack"}],
            )
        ]
        traj = build_trajectory_from_records(
            records, session_id="s", agent_name="a", agent_version="0"
        )
        user_messages = [s["message"] for s in traj["steps"] if s["source"] == "user"]
        assert user_messages == ["real user prompt"], (
            f"F3: phantom envelope-only user step must be dropped; got {user_messages!r}"
        )

    def test_per_step_metrics_on_agent_steps(self) -> None:
        """F4: every agent step that originates from a real LlmCall carries
        per-step metrics (prompt/completion/cached tokens, cost_usd). Non-
        agent steps do NOT carry metrics.
        """
        records = [
            _llm_record(
                start_ns=1_000_000_000,
                end_ns=2_000_000_000,
                input_messages=[
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "u1"},
                ],
                output_messages=[{"role": "assistant", "content": "first"}],
                prompt_tokens=100,
                completion_tokens=10,
                cached_tokens=20,
                cost=0.0012,
            ),
            _llm_record(
                start_ns=3_000_000_000,
                end_ns=4_000_000_000,
                input_messages=[
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "u1"},
                    {"role": "assistant", "content": "first"},
                    {"role": "user", "content": "u2"},
                ],
                output_messages=[{"role": "assistant", "content": "second"}],
                prompt_tokens=200,
                completion_tokens=20,
                cached_tokens=50,
                cost=0.0034,
            ),
        ]
        traj = build_trajectory_from_records(
            records, session_id="s", agent_name="a", agent_version="0"
        )
        agent_steps = [s for s in traj["steps"] if s["source"] == "agent"]
        assert len(agent_steps) == 2
        assert agent_steps[0]["metrics"]["prompt_tokens"] == 100
        assert agent_steps[0]["metrics"]["completion_tokens"] == 10
        assert agent_steps[0]["metrics"]["cached_tokens"] == 20
        assert agent_steps[0]["metrics"]["cost_usd"] == pytest.approx(0.0012)
        assert agent_steps[1]["metrics"]["prompt_tokens"] == 200
        assert agent_steps[1]["metrics"]["completion_tokens"] == 20
        assert agent_steps[1]["metrics"]["cached_tokens"] == 50
        assert agent_steps[1]["metrics"]["cost_usd"] == pytest.approx(0.0034)
        # Non-agent steps do NOT carry metrics.
        for s in traj["steps"]:
            if s["source"] != "agent":
                assert "metrics" not in s, (
                    f"F4: metrics must only appear on agent steps; found on {s['source']}"
                )
        # final_metrics totals still sum correctly.
        assert traj["final_metrics"]["total_prompt_tokens"] == 300
        assert traj["final_metrics"]["total_completion_tokens"] == 30
        assert traj["final_metrics"]["total_cached_tokens"] == 70

    def test_agent_step_carries_llm_call_count_one(self) -> None:
        """F5: every assistant step that originates from a real LlmCall has
        ``llm_call_count == 1``. Non-agent steps do not carry this field.
        """
        records = [
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
        traj = build_trajectory_from_records(
            records, session_id="s", agent_name="a", agent_version="0"
        )
        for s in traj["steps"]:
            if s["source"] == "agent":
                assert s.get("llm_call_count") == 1
            else:
                assert "llm_call_count" not in s

    def test_final_metrics_promotes_total_cost_usd_to_top_level(self) -> None:
        """F6: ``total_cost_usd`` is a spec-defined top-level field of
        ``FinalMetricsSchema`` (not under ``extra``).
        """
        records = [
            _llm_record(
                start_ns=1_000_000_000,
                end_ns=2_000_000_000,
                input_messages=[
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "u"},
                ],
                output_messages=[{"role": "assistant", "content": "a"}],
                cost=0.001234,
            ),
            _llm_record(
                start_ns=3_000_000_000,
                end_ns=4_000_000_000,
                input_messages=[
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "u"},
                    {"role": "assistant", "content": "a"},
                    {"role": "user", "content": "u2"},
                ],
                output_messages=[{"role": "assistant", "content": "b"}],
                cost=0.000766,
            ),
        ]
        traj = build_trajectory_from_records(
            records, session_id="s", agent_name="a", agent_version="0"
        )
        fm = traj["final_metrics"]
        assert fm["total_cost_usd"] == pytest.approx(0.002)
        # Promoted out of extra.
        assert "total_cost_usd" not in fm.get("extra", {})

    def test_reasoning_content_attached_to_agent_step_when_present(self) -> None:
        """F7: ``llm.reasoning_content`` from the LLM span (set by the
        litellm patch for reasoning models) MUST flow to the matching
        agent step's ``reasoning_content`` field.
        """
        rec = _llm_record(
            start_ns=1_000_000_000,
            end_ns=2_000_000_000,
            input_messages=[
                {"role": "system", "content": "s"},
                {"role": "user", "content": "u"},
            ],
            output_messages=[{"role": "assistant", "content": "a"}],
        )
        rec.attrs["llm.reasoning_content"] = "I will think about this carefully."
        traj = build_trajectory_from_records(
            [rec], session_id="s", agent_name="a", agent_version="0"
        )
        agent_step = next(s for s in traj["steps"] if s["source"] == "agent")
        assert agent_step.get("reasoning_content") == "I will think about this carefully."

    def test_trajectory_carries_root_trajectory_id(self) -> None:
        """F8: ``trajectory_id`` is recommended on standalone trajectories
        per v1.7. We derive it from ``session_id``.
        """
        records = [
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
        traj = build_trajectory_from_records(
            records, session_id="s-123", agent_name="a", agent_version="0"
        )
        assert traj.get("trajectory_id") == "s-123"

    def test_trajectory_id_set_on_empty_trajectory(self) -> None:
        """F8 empty-call branch: ``trajectory_id`` must also be set when
        there are no records (the early-return branch).
        """
        traj = build_trajectory_from_records(
            [], session_id="s-empty", agent_name="a", agent_version="0"
        )
        assert traj.get("trajectory_id") == "s-empty"

    def test_empty_tool_result_does_not_attach_observation(self) -> None:
        """MR-303 review fix: when ``_safe_serialize_execution_result``
        emits a JSON blob whose stdout/stderr/returned_value are all empty
        (e.g. ``print()`` with no args), the exporter must NOT attach the
        raw JSON metadata as observation content. The observation should
        be omitted entirely so consumers don't see ``{"stdout": "", ...}``
        rendered as a tool result.
        """
        records = [
            _llm_record(
                start_ns=1_000_000_000,
                end_ns=2_000_000_000,
                input_messages=[
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "u"},
                ],
                output_messages=[
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "tool_call_id": "call_empty",
                                "function_name": "execute_python",
                                "arguments": {"code": ""},
                            }
                        ],
                    }
                ],
            ),
            _tool_record(
                start_ns=2_100_000_000,
                end_ns=2_200_000_000,
                tool_call_id="call_empty",
                result=json.dumps({"stdout": "", "stderr": "", "returned_value": None}),
            ),
        ]
        traj = build_trajectory_from_records(
            records, session_id="s", agent_name="a", agent_version="0"
        )
        agent_step = next(s for s in traj["steps"] if s.get("tool_calls"))
        # No observation attached for an empty-output execution.
        assert "observation" not in agent_step, (
            "Empty execution result must not produce an observation; "
            "got an observation with content="
            f"{agent_step.get('observation', {}).get('results', [{}])[0].get('content')!r}"
        )

    def test_tool_metadata_keyed_by_tool_call_id_for_multi_tool_step(self) -> None:
        """MR-303 review fix: when an agent step issues multiple tool_calls,
        ``extra.tool_metadata`` must be a dict keyed by ``tool_call_id`` so
        each call's duration/result_type is preserved. The previous shared-
        dict layout dropped all but the first tool's metadata.
        """
        records = [
            _llm_record(
                start_ns=1_000_000_000,
                end_ns=2_000_000_000,
                input_messages=[
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": "u"},
                ],
                output_messages=[
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "tool_call_id": "call_first",
                                "function_name": "execute_python",
                                "arguments": {"code": "1"},
                            },
                            {
                                "tool_call_id": "call_second",
                                "function_name": "execute_python",
                                "arguments": {"code": "2"},
                            },
                        ],
                    }
                ],
            ),
            _tool_record(
                start_ns=2_000_000_000,
                end_ns=2_100_000_000,
                tool_call_id="call_first",
                result_type="PythonOutput",
                result=json.dumps({"stdout": "one\n", "stderr": "", "returned_value": None}),
            ),
            _tool_record(
                start_ns=2_100_000_000,
                end_ns=2_300_000_000,
                tool_call_id="call_second",
                result_type="ShellOutput",
                result=json.dumps({"stdout": "two\n", "stderr": "", "returned_value": None}),
            ),
        ]
        traj = build_trajectory_from_records(
            records, session_id="s", agent_name="a", agent_version="0"
        )
        agent_step = next(s for s in traj["steps"] if s.get("tool_calls"))
        tool_meta = agent_step["extra"]["tool_metadata"]
        # Per-tool entries, not a shared flat dict.
        assert set(tool_meta.keys()) == {"call_first", "call_second"}
        assert tool_meta["call_first"]["duration_seconds"] == pytest.approx(0.1)
        assert tool_meta["call_first"]["result_type"] == "PythonOutput"
        assert tool_meta["call_second"]["duration_seconds"] == pytest.approx(0.2)
        assert tool_meta["call_second"]["result_type"] == "ShellOutput"


class TestCodeExecutionSpanAttributes:
    """Tests pinning the hook contract that F1 depends on.

    F1 keys TOOL spans by ``tool_call_id`` (``ToolCall.tool_call_id``). The
    attribute survives only because ``before_code_execution`` routes the
    ``tool_call_id`` kwarg through the generic kwargs-auto-attribute loop
    at ``_hooks_impl.py:425-433`` — which happens to leave the attribute
    name unchanged because the kwarg name starts with ``"tool"``. A future
    refactor of that loop would silently break observation pairing in the
    ATIF exporter. Pin it here.
    """

    def test_code_execution_span_carries_tool_call_id_attribute(self) -> None:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            SimpleSpanProcessor,
        )
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        from nemo_oo_agents.tracing._hooks_impl import OpenInferenceHooks

        # Stand up a hermetic tracer that captures spans into memory.
        in_mem = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(in_mem))
        tracer = provider.get_tracer(__name__)

        hooks = OpenInferenceHooks(tracer=tracer)

        class _DummyAgent:
            pass

        # Call the hook the same way the codeact strategy does.
        ctx = hooks.before_code_execution(
            agent=_DummyAgent(),
            code="print('hello')",
            execution_id="exec_test",
            generation_id=None,
            tool_call_id="call_PINNED",
        )
        hooks.after_code_execution(
            agent=_DummyAgent(),
            code="print('hello')",
            result=None,
            exception=None,
            context=ctx,
            execution_id="exec_test",
        )

        spans = in_mem.get_finished_spans()
        assert spans, "code_execution span was not recorded"
        code_exec_span = next((s for s in spans if s.name == "code_execution"), None)
        assert code_exec_span is not None
        # Pin: the attribute name MUST be exactly ``tool_call_id`` (not
        # ``tool.tool_call_id``), because the ATIF exporter keys ToolCall
        # by ``rec.attrs["tool_call_id"]``.
        assert code_exec_span.attributes is not None
        assert code_exec_span.attributes.get("tool_call_id") == "call_PINNED", (
            "F1 dependency: code_execution span must carry tool_call_id attribute "
            "unchanged from the kwarg name. ATIF observation pairing breaks otherwise."
        )

    def test_stderr_only_execution_result_uses_json_serializer(self) -> None:
        """MR-303 review fix: when a successful execution emits only to
        stderr (no stdout, no returned_value), ``after_code_execution`` must
        still route through ``_safe_serialize_execution_result`` so the
        ``result`` attribute carries parseable JSON.  The previous guard
        ignored stderr-only results and fell back to the generic repr
        serializer, breaking the ATIF exporter's observation parsing.
        """
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        from nemo_oo_agents.tracing._hooks_impl import OpenInferenceHooks

        in_mem = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(in_mem))
        tracer = provider.get_tracer(__name__)
        hooks = OpenInferenceHooks(tracer=tracer)

        class _DummyAgent:
            pass

        class _StderrOnlyResult:
            stderr = "warning: deprecated"

        ctx = hooks.before_code_execution(
            agent=_DummyAgent(),
            code="warn()",
            execution_id="exec_stderr",
            generation_id=None,
            tool_call_id="call_stderr",
        )
        hooks.after_code_execution(
            agent=_DummyAgent(),
            code="warn()",
            result=_StderrOnlyResult(),
            exception=None,
            context=ctx,
            execution_id="exec_stderr",
        )

        code_exec_span = next(s for s in in_mem.get_finished_spans() if s.name == "code_execution")
        result_attr = code_exec_span.attributes["result"]
        # Must be parseable JSON, not a repr-style string.
        parsed = json.loads(result_attr)
        assert parsed.get("stderr") == "warning: deprecated"


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
