# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for TUI ``RespondResult`` return reasons."""

from __future__ import annotations

import json

import pytest
from nemo_oo_agents_cli.tui.agent import RespondReason, RespondResult
from nemo_oo_agents_cli.tui.output import StopReasonOutput
from pydantic import ValidationError

from nemo_oo_agents import Agent, strategy
from nemo_oo_agents.agentdoc.visibility import is_hidden_method
from nemo_oo_agents.config import CodeActConfig
from nemo_oo_agents.strategies import CodeActStrategy
from nemo_oo_agents.unifiedllm import FakeLLMClient, LLMResponse, ToolCall


def _resp(code: str) -> LLMResponse:
    return LLMResponse(
        raw_response=None,
        content="",
        tool_calls=[
            ToolCall(id="call_1", name="execute_python", arguments=json.dumps({"code": code}))
        ],
        finish_reason="tool_calls",
        assistant_message={"role": "assistant", "content": ""},
    )


def test_respond_result_accepts_reason_enum_and_explanation() -> None:
    result = RespondResult(
        kind=RespondReason.DONE,
        explanation="answered the user; waiting for the next message",
    )

    assert result.kind is RespondReason.DONE
    assert result.explanation == "answered the user; waiting for the next message"


def test_respond_result_accepts_existing_string_kind() -> None:
    result = RespondResult(kind="WAIT", explanation="waiting for CI")

    assert result.kind is RespondReason.WAIT
    assert result.explanation == "waiting for CI"


def test_respond_result_requires_explanation() -> None:
    with pytest.raises(ValidationError, match="explanation"):
        RespondResult(kind=RespondReason.GET_USER_INPUT)


def test_respond_result_rejects_blank_explanation() -> None:
    with pytest.raises(ValidationError, match="explanation"):
        RespondResult(kind=RespondReason.DONE, explanation="   ")


def test_stop_reason_output_formats_wait_done_and_input_reasons() -> None:
    waiting = StopReasonOutput("WAIT", "pytest job ci-42 is still running")
    done = StopReasonOutput("DONE", "implemented the feature")
    need_input = StopReasonOutput("NEED_INPUT", "need the target branch")
    legacy_input = StopReasonOutput("GET_USER_INPUT", "need the target branch")

    assert waiting.label == "waiting"
    assert waiting.display_text() == "∴ waiting: pytest job ci-42 is still running"
    assert waiting.to_json() == {
        "type": "stop_reason",
        "kind": "WAIT",
        "label": "waiting",
        "explanation": "pytest job ci-42 is still running",
        "text": "∴ waiting: pytest job ci-42 is still running",
    }
    assert done.label == "done"
    assert done.display_text() == "∴ done: implemented the feature"
    assert need_input.label == "need input"
    assert need_input.display_text() == "∴ need input: need the target branch"
    assert legacy_input.label == "need input"


def test_stop_reason_output_sanitizes_control_sequences() -> None:
    output = StopReasonOutput(
        "WAIT",
        "running\x1b]52;c;clipboard\x07\n\x1b[2Jdone [not markup]",
    )

    rendered = output.display_text()
    assert rendered == "∴ waiting: running done [not markup]"
    assert "\x1b" not in rendered
    assert "\x07" not in rendered
    assert "\n" not in rendered
    assert output.to_json()["explanation"] == "running done [not markup]"


def test_stop_reason_output_helper_api_is_hidden() -> None:
    assert is_hidden_method(StopReasonOutput.label)
    assert is_hidden_method(StopReasonOutput.display_text)
    assert is_hidden_method(StopReasonOutput.to_json)


@pytest.mark.asyncio
async def test_handle_can_return_reason_enum_with_explanation_from_inline_return_result() -> None:
    class TestAgent(Agent, llm=FakeLLMClient()):
        @strategy(CodeActStrategy(config=CodeActConfig(max_retries=2)))
        async def handle(self) -> RespondResult:
            """Return a TUI dispatcher decision."""
            ...

    agent = TestAgent(
        llm=FakeLLMClient(
            scripted_responses=[
                _resp(
                    "return_result(RespondReason.NEED_INPUT, "
                    'explanation="answered; waiting for more user input")'
                )
            ]
        )
    )

    result = await agent.handle()

    assert result.kind is RespondReason.NEED_INPUT
    assert result.explanation == "answered; waiting for more user input"
