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
from nemo_oo_agents.events import Message, PythonOutput
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


class TestTotalTokensCalibrated:
    """total_tokens must carry the SAME per-model calibration as events_tokens.

    Root cause of "events_tokens > total_tokens" (session d2a3557e): the message-
    level overwrite in _build_messages used RAW litellm.token_counter while
    events_tokens was counted with the calibrated client counter. For a model
    litellm under-counts (calibration ratio > 1), total_tokens then read smaller
    than events_tokens — impossible for a documented blocks+events sum — and the
    TUI ctx%% / TokenBudgetSummarizer trigger saw a number ~ratio x too small.
    """

    @pytest.mark.asyncio
    async def test_total_tokens_uses_calibration_ratio(self):
        from nemo_oo_agents.unifiedllm.unifiedllm import _token_calibration

        model = "anthropic/claude-3-5-sonnet-20240620"
        # Snapshot the process-global calibration state for this model so the
        # forced ratio can't leak into later (order-dependent) tests.
        _prev_ratio = _token_calibration._ratios.get(model)
        _prev_actual = _token_calibration._last_actual.get(model)
        # Force a known calibration ratio for this model (API reported ~2x raw).
        _token_calibration.update(model, estimated=100_000, actual=200_000)
        try:
            ratio = _token_calibration.ratio(model)
            assert ratio > 1.5  # litellm under-counts -> calibration scales up

            llm = _mk_calibrating_llm(1_000_000)
            llm.model = model

            class A(Agent, llm=llm):
                async def respond(self, prompt: str) -> str:
                    """Respond to {prompt}."""
                    ...

            agent = A()
            for i in range(40):
                agent.event_manager.add(Message(content=f"event {i}: " + ("lorem ipsum " * 30)))

            # respond() raises GenerationError (the fake LLM has no scripted
            # response), but _build_messages has already published the calibrated
            # _last_context_stats by then. Catch ONLY the expected failure so a
            # real break (anything else) fails the test instead of passing silently.
            from nemo_oo_agents.errors import GenerationError

            with pytest.raises(GenerationError):
                await agent.respond("hi")

            stats = agent.runtime._last_context_stats
            assert stats is not None
            # The headline must reflect the calibrated (API-matching) size, so it
            # can never be smaller than the events component it supposedly contains.
            assert stats.total_tokens >= stats.events_tokens
        finally:
            # Restore the singleton's per-model state.
            if _prev_ratio is None:
                _token_calibration._ratios.pop(model, None)
            else:
                _token_calibration._ratios[model] = _prev_ratio
            if _prev_actual is None:
                _token_calibration._last_actual.pop(model, None)
            else:
                _token_calibration._last_actual[model] = _prev_actual
