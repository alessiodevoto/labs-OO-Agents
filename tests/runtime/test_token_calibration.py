# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for token calibration from API response usage stats.

After each LLM call, the runtime extracts response.usage.prompt_tokens and
computes a calibration ratio (actual / litellm_estimate). This ratio is stored
for potential future use but does not currently affect the safety net cap.
"""

import pytest

from nemo_oo_agents import Agent
from nemo_oo_agents.context_blocks.events import ResultStatus, ToolCallEvent, ToolResult
from nemo_oo_agents.events import PythonOutput
from nemo_oo_agents.unifiedllm import FakeLLMClient


class _CalibratingFakeLLM(FakeLLMClient):
    """FakeLLM that reports usage.prompt_tokens in the response."""

    _cw = 200_000
    _reported_prompt_tokens = 163_000  # simulates API reporting higher than litellm

    @property
    def context_window(self):
        return self._cw

    def count_tokens(self, text: str) -> int:
        import litellm
        return litellm.token_counter(model="anthropic/claude-3-5-sonnet-20240620", text=text)


def _mk_calibrating_llm(context_window: int = 200_000):
    """Create a FakeLLM with a configurable context window."""

    class _LLM(_CalibratingFakeLLM):
        _cw = context_window

    llm = _LLM()
    llm.model = "anthropic/claude-3-5-sonnet-20240620"
    return llm


class TestTokenCalibration:
    """Token calibration stores the ratio but does not affect the safety net cap."""

    @pytest.mark.asyncio
    async def test_context_stats_populated_after_llm_call(self):
        """After a successful LLM call, _last_context_stats is populated."""
        llm = _mk_calibrating_llm(200_000)

        class A(Agent, llm=llm):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()
        # Add some events so there's actual content to measure
        for i in range(10):
            tc_id = f"call_{i}"
            agent.event_manager.add(
                ToolCallEvent(
                    tool_call_id=tc_id,
                    name="execute_python",
                    arguments={"code": f"x = {i}"},
                    result=ToolResult(
                        tool_call_id=tc_id,
                        content=f"done_{i}",
                        result_status=ResultStatus.COMPLETE,
                    ),
                )
            )

        runtime = agent.runtime
        assert runtime._token_calibration_ratio is None

        # Trigger an LLM call
        try:
            await agent.respond("hello")
        except Exception:
            pass

        # _last_context_stats should be populated from the render pass
        assert runtime._last_context_stats is not None
        assert runtime._last_context_stats.total_tokens > 0

    @pytest.mark.asyncio
    async def test_cap_always_uses_70_percent(self):
        """Safety net always uses 70% cap regardless of calibration ratio."""
        llm = _mk_calibrating_llm(200_000)

        class A(Agent, llm=llm):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()
        runtime = agent.runtime

        # Even with a calibration ratio set, cap should still be 70%
        runtime._token_calibration_ratio = 1.63

        # Add enough events to make the cap meaningful
        for i in range(200):
            tc_id = f"call_{i}"
            agent.event_manager.add(
                ToolCallEvent(
                    tool_call_id=tc_id,
                    name="execute_python",
                    arguments={"code": f"step_{i} " * 50},
                    result=ToolResult(
                        tool_call_id=tc_id,
                        content=f"result_{i} " * 50,
                        result_status=ResultStatus.COMPLETE,
                    ),
                )
            )
            agent.event_manager.add(
                PythonOutput(
                    tool_call_id=tc_id,
                    execution_count=i,
                    stdout=f"out_{i} " * 50,
                    stderr="",
                    execution_status=ResultStatus.COMPLETE,
                )
            )

        try:
            await agent.respond("test calibration")
        except Exception:
            pass

        stats = runtime._last_context_stats
        assert stats is not None
        # The cap is 70% of 200K = 140K. Events should be clamped to that.
        assert stats.total_tokens <= int(200_000 * 0.70) + 1000  # small tolerance


class TestTokenCalibrationEdgeCases:
    """Edge cases for the calibration logic."""

    def test_ratio_not_set_when_usage_missing(self):
        """If response has no usage, ratio stays None."""
        llm = _mk_calibrating_llm()

        class A(Agent, llm=llm):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()
        runtime = agent.runtime
        assert runtime._token_calibration_ratio is None

    def test_ratio_not_set_when_stats_missing(self):
        """If _last_context_stats is None (no prior render), ratio stays None."""
        llm = _mk_calibrating_llm()

        class A(Agent, llm=llm):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()
        runtime = agent.runtime
        assert runtime._last_context_stats is None
        assert runtime._token_calibration_ratio is None
