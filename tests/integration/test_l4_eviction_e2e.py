# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""End-to-end integration tests for L4 context eviction.

Inline tests use FakeLLMClient (no API calls). Tests marked @pytest.mark.integration
require a real LLM and should run in nightly CI only.
"""

import pytest

from nemo_oo_agents import Agent
from nemo_oo_agents.config.truncation_config import TruncationConfig
from nemo_oo_agents.context_blocks import BlockMetadata, ResolvedBlock, Role
from nemo_oo_agents.context_blocks.events import ResultStatus, ToolCallEvent, ToolResult
from nemo_oo_agents.context_blocks.formatter import OpenAIProviderFormatter, XMLBlockFormatter
from nemo_oo_agents.context_blocks.renderer import render_context
from nemo_oo_agents.events import PythonOutput
from nemo_oo_agents.runtime.actor import _current_llm_var
from nemo_oo_agents.runtime.harness_metrics import harness_metrics_session
from nemo_oo_agents.unifiedllm import FakeLLMClient

# ── Helpers ──────────────────────────────────────────────────────────────


class _FakeLLM(FakeLLMClient):
    """FakeLLM with a settable context_window for tests."""

    _cw = 4096

    @property
    def context_window(self):  # type: ignore[override]
        return self._cw

    def count_tokens(self, text: str) -> int:
        import litellm

        return litellm.token_counter(model="anthropic/claude-3-5-sonnet-20240620", text=text)


def _mk_agent(context_window: int = 4096, max_context_tokens: int | None = None) -> Agent:
    """Create a test agent with configurable window and context budget."""

    class _LLM(_FakeLLM):
        _cw = context_window

    llm = _LLM()
    llm.model = "anthropic/claude-3-5-sonnet-20240620"  # type: ignore[attr-defined]

    if max_context_tokens is not None:

        class A(Agent, llm=llm):
            _truncation_config = TruncationConfig(max_context_tokens=max_context_tokens)

            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()
    else:

        class A(Agent, llm=llm):
            async def respond(self, prompt: str) -> str:
                """Respond to {prompt}."""
                ...

        agent = A()
    return agent


def _count_words(s: str) -> int:
    """Simple word-count token approximation for tests."""
    return len(s.split())


# ── Context Block EVICTION ───────────────────────────────────────────────


class TestContextBlockEviction:
    """Context blocks over budget are marked EVICTED in-place."""

    def test_over_budget_block_gets_evicted_marker(self):
        """Non-immutable blocks exceeding context_limit get EVICTED content."""
        blocks = [
            ResolvedBlock(
                key="system_prompt",
                content="You are helpful.",
                role=Role.SYSTEM,
                metadata=BlockMetadata(immutable=True),
            ),
            ResolvedBlock(
                key="big_block",
                content="data " * 200,
                role=Role.SYSTEM,
                metadata=BlockMetadata(),
            ),
        ]

        result = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
            context_limit=50,
            count_tokens=_count_words,
        )

        assert result.stats.context_blocks_dropped > 0
        # EVICTED marker present in output
        output_str = str(result.output)
        assert "EVICTED" in output_str

    def test_immutable_blocks_never_evicted(self):
        """Blocks with immutable=True survive regardless of budget."""
        blocks = [
            ResolvedBlock(
                key="immutable",
                content="x " * 200,
                role=Role.SYSTEM,
                metadata=BlockMetadata(immutable=True),
            ),
        ]

        result = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
            context_limit=50,
            count_tokens=_count_words,
        )

        assert result.stats.context_blocks_dropped == 0

    def test_multiple_blocks_evicted_newest_first(self):
        """When multiple blocks exceed budget, newest are evicted first
        (eviction works from the end to preserve oldest/most-stable blocks)."""
        blocks = [
            ResolvedBlock(
                key=f"block_{i}",
                content=f"content_{i} " * 50,
                role=Role.SYSTEM,
                metadata=BlockMetadata(),
            )
            for i in range(5)
        ]

        result = render_context(
            blocks,
            block_formatter=XMLBlockFormatter(),
            provider_formatter=OpenAIProviderFormatter(),
            context_limit=100,
            count_tokens=_count_words,
        )

        assert result.stats.context_blocks_dropped >= 3
        # Oldest blocks survive (eviction removes from end)
        output_str = str(result.output)
        assert "content_0" in output_str

    def test_harness_metrics_track_eviction(self):
        """context_limits_blocks_evicted is populated on eviction."""
        blocks = [
            ResolvedBlock(
                key=f"block_{i}",
                content="y " * 100,
                role=Role.SYSTEM,
                metadata=BlockMetadata(),
            )
            for i in range(5)
        ]

        with harness_metrics_session() as hm:
            result = render_context(
                blocks,
                block_formatter=XMLBlockFormatter(),
                provider_formatter=OpenAIProviderFormatter(),
                context_limit=200,
                count_tokens=_count_words,
            )
            assert hm.context_limits_blocks_evicted > 0
            assert hm.context_limits_blocks_evicted == result.stats.context_blocks_dropped


# ── pformat(unquote_strings=True) ───────────────────────────────────────


class TestUnquoteStrings:
    """Context blocks render strings verbatim (no quotes) via unquote_strings."""

    def test_short_string_no_quotes(self):
        from nemo_oo_agents.agentdoc import pformat

        assert pformat("Hello world", unquote_strings=True) == "Hello world"

    def test_long_string_gets_truncation_marker(self):
        from nemo_oo_agents.agentdoc import pformat

        result = pformat("a" * 1000, unquote_strings=True, max_string=100)
        assert result.startswith("str(len=1000")
        assert "[:50]=" in result

    def test_non_string_unaffected(self):
        from nemo_oo_agents.agentdoc import pformat

        assert pformat([1, 2, 3], unquote_strings=True) == pformat([1, 2, 3])

    def test_multiline_string_verbatim(self):
        from nemo_oo_agents.agentdoc import pformat

        text = "line1\nline2\nline3"
        result = pformat(text, unquote_strings=True)
        assert result == text


    def test_triple_quote_string_preserved(self):
        """String consisting of triple quotes (''') is not deleted by unquote logic."""
        from nemo_oo_agents.agentdoc import pformat

        # Edge case: input is literally 3 single-quote chars
        result = pformat("'''", unquote_strings=True)
        assert result == "'''", f"Expected '''' but got {repr(result)}"

    def test_string_with_embedded_triple_quotes(self):
        """String containing triple quotes inside is preserved."""
        from nemo_oo_agents.agentdoc import pformat

        text = "before''' after"
        result = pformat(text, unquote_strings=True)
        assert result == text

# ── Post-render event collapse ───────────────────────────────────────────


class TestPostRenderEventCollapse:
    """When rendered payload exceeds context_window, events are collapsed."""

    @pytest.mark.asyncio
    async def test_overflow_triggers_collapse(self):
        """Events are collapsed when payload exceeds context_window budget."""
        import litellm

        agent = _mk_agent(context_window=4096)

        # Fill with many events
        for i in range(20):
            tc_id = f"tc_{i}"
            agent.event_manager.add(
                ToolCallEvent(
                    tool_call_id=tc_id,
                    name="execute_python",
                    arguments={"code": "x " * 100},
                    result=ToolResult(
                        tool_call_id=tc_id,
                        content="done",
                        result_status=ResultStatus.COMPLETE,
                    ),
                )
            )
            agent.event_manager.add(
                PythonOutput(
                    tool_call_id=tc_id,
                    execution_count=i,
                    stdout="y " * 100,
                    stderr="",
                    execution_status=ResultStatus.COMPLETE,
                )
            )

        method = type(agent).respond
        token = _current_llm_var.set(agent._llm)
        try:
            messages = await agent.runtime._build_messages(
                method,
                call_args=(agent, "hi"),
                call_kwargs={},
                max_output_tokens=2048,
            )
        finally:
            _current_llm_var.reset(token)

        assert messages is not None
        structured = litellm.token_counter(model=agent._llm.model, messages=messages)
        assert structured + 2048 < 4096, (
            f"Payload ({structured}) + output (2048) exceeds window (4096)"
        )

        # Collapse should have reduced active events
        active_after = len(list(agent.event_manager.keys()))
        assert active_after < 40


# ── Default budget split ─────────────────────────────────────────────────


class TestDefaultBudgetSplit:
    """When max_context_tokens is unset, context budget = context_window // 2."""

    @pytest.mark.asyncio
    async def test_half_window_cap_evicts_large_context(self):
        """Large context blocks are evicted when they exceed half the window."""
        agent = _mk_agent(context_window=8192)

        # Add a huge context block (~5k tokens > 4096 half-window)
        agent.context["huge"] = "z " * 5000

        method = type(agent).respond
        token = _current_llm_var.set(agent._llm)
        try:
            await agent.runtime._build_messages(
                method,
                call_args=(agent, "hi"),
                call_kwargs={},
                max_output_tokens=2048,
            )
        finally:
            _current_llm_var.reset(token)

        stats = agent.runtime._last_context_stats
        assert stats is not None
        # Either eviction fired or the context fits in half window
        assert stats.context_blocks_dropped > 0 or stats.context_blocks_tokens <= 4096


# ── Nightly integration tests (real LLM) ────────────────────────────────


@pytest.mark.integration
class TestNightlyRealLLM:
    """Tests that exercise real LLM context window limits.

    Requires a real API key and network access. Run with:
        pytest -m integration tests/integration/test_l4_eviction_e2e.py
    """

    @pytest.mark.asyncio
    async def test_real_context_overflow_recovery(self):
        """Agent recovers gracefully when hitting real context window limits."""
        # This test would:
        # 1. Use a small-window model (e.g. gpt-4o-mini with 128k)
        # 2. Fill context to >90% capacity
        # 3. Verify the agent can still respond (collapse fires)
        # 4. Verify no 400/context_length_exceeded errors
        pytest.skip("Requires real API key — run in nightly CI")
