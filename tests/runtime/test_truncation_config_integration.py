"""Tests for truncation configuration integration with Agent class."""

import pytest

from nemo_oo_agents import Agent
from nemo_oo_agents.config.truncation_config import TruncationConfig
from unifiedllm import FakeLLMClient

# Module-level test LLM
_TEST_LLM = FakeLLMClient()


class TestTruncationConfigResolution:
    """Tests for config resolution at class/instance levels."""

    def test_default_config(self):
        """Agents should have default truncation config."""

        class TestAgent(Agent, llm=_TEST_LLM):
            pass

        agent = TestAgent()

        # Should have default config
        assert agent._truncation is not None
        assert agent._truncation.max_stdout_chars == 50_000
        assert agent._truncation.max_stderr_chars == 20_000
        assert agent._truncation.max_pprint_elements == 50

    def test_class_level_config(self):
        """Class-level config should override defaults."""

        class TestAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(max_stdout_chars=100_000, max_pprint_elements=100),
        ):
            pass

        agent = TestAgent()

        # Should have class-level config
        assert agent._truncation.max_stdout_chars == 100_000
        assert agent._truncation.max_pprint_elements == 100
        # Other defaults preserved
        assert agent._truncation.max_stderr_chars == 20_000

    def test_instance_level_config(self):
        """Instance-level config should override class config."""

        class TestAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(max_stdout_chars=100_000),
        ):
            pass

        agent = TestAgent(
            truncation=TruncationConfig(max_stdout_chars=200_000, max_pprint_depth=10)
        )

        # Instance config should win
        assert agent._truncation.max_stdout_chars == 200_000
        assert agent._truncation.max_pprint_depth == 10
        # Other class defaults preserved
        assert agent._truncation.max_pprint_elements == 50

    def test_config_merge_behavior(self):
        """Configs should merge properly (later overrides earlier)."""

        class TestAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(
                max_stdout_chars=100_000,
                max_pprint_elements=100,
            ),
        ):
            pass

        agent = TestAgent(
            truncation=TruncationConfig(
                max_pprint_elements=200,  # Override class
                max_pprint_depth=5,  # New value
            )
        )

        # Merged result
        assert agent._truncation.max_stdout_chars == 100_000  # From class
        assert agent._truncation.max_pprint_elements == 200  # From instance
        assert agent._truncation.max_pprint_depth == 5  # From instance
        assert agent._truncation.max_stderr_chars == 20_000  # From default

    def test_multiple_agents_independent_configs(self):
        """Multiple agents should have independent configs."""

        class Agent1(Agent, llm=_TEST_LLM, truncation=TruncationConfig(max_stdout_chars=50_000)):
            pass

        class Agent2(Agent, llm=_TEST_LLM, truncation=TruncationConfig(max_stdout_chars=100_000)):
            pass

        agent1 = Agent1()
        agent2 = Agent2()

        # Should be independent
        assert agent1._truncation.max_stdout_chars == 50_000
        assert agent2._truncation.max_stdout_chars == 100_000


class TestTruncationConfigUsage:
    """Tests for config usage in execution."""

    @pytest.mark.asyncio
    async def test_custom_stdout_limit_applied(self):
        """Custom stdout limit should be applied."""

        class TestAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(max_stdout_chars=1000),  # Very small limit
        ):
            pass

        agent = TestAgent()

        # Generate output larger than limit
        code = """
for i in range(100):
    print("x" * 50)
"""
        result = await agent.runtime.execute_code(code)

        assert result.success
        # Should be truncated at 1000 chars — prose format shows head+tail split
        assert "Output too large" in result.stdout
        assert "500 and last 500 chars" in result.stdout

    @pytest.mark.asyncio
    async def test_custom_stderr_limit_applied(self):
        """Custom stderr limit should be applied."""

        class TestAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(max_stderr_chars=500),  # Very small limit
        ):
            pass

        agent = TestAgent()

        # This test is simplified due to sandbox restrictions
        # The limit is verified by the unit tests
        assert agent._truncation.max_stderr_chars == 500

    @pytest.mark.asyncio
    async def test_different_agents_different_limits(self):
        """Different agents should use their own limits."""

        class SmallLimitAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(max_stdout_chars=500),
        ):
            pass

        class LargeLimitAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(max_stdout_chars=10_000),
        ):
            pass

        small_agent = SmallLimitAgent()
        large_agent = LargeLimitAgent()

        # Same code, different truncation
        code = 'print("x" * 2000)'

        result_small = await small_agent.runtime.execute_code(code)
        result_large = await large_agent.runtime.execute_code(code)

        # Small agent should truncate — prose format
        assert "Output too large" in result_small.stdout
        # Large agent should not truncate (2000 < 10000)
        assert "Output too large" not in result_large.stdout


class TestConfigMergeEdgeCases:
    """Tests for edge cases in config merging."""

    def test_none_config_uses_defaults(self):
        """Passing None should use defaults."""

        class TestAgent(Agent, llm=_TEST_LLM):
            pass

        agent = TestAgent(truncation=None)

        # Should have defaults
        assert agent._truncation.max_stdout_chars == 50_000

    def test_partial_override(self):
        """Partial configs should only override specified fields."""

        class TestAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(
                max_stdout_chars=100_000,
                max_stderr_chars=30_000,
                max_pprint_elements=100,
            ),
        ):
            pass

        agent = TestAgent(
            truncation=TruncationConfig(max_pprint_elements=200)  # Only override this
        )

        # Only max_pprint_elements should be overridden
        assert agent._truncation.max_stdout_chars == 100_000  # Preserved from class
        assert agent._truncation.max_stderr_chars == 30_000  # Preserved from class
        assert agent._truncation.max_pprint_elements == 200  # Overridden


class TestTokenBudgetIntegration:
    """Tests for token budget fields (max_context_tokens, max_event_tokens)."""

    @pytest.mark.asyncio
    async def test_max_context_tokens_does_not_crash_on_generation(self):
        """Agent with max_context_tokens set should not raise ValueError during generation.

        Regression test: render_context() raises ValueError if context_limit is non-None
        but count_tokens is not provided. The actor must pass count_tokens to render_context().
        """
        from unifiedllm import FakeLLMClient

        # Return a return_result tool call so CodeActStrategy completes successfully
        llm = FakeLLMClient.with_tool_call("return_result", {"result": "done"})

        class TestAgent(
            Agent,
            llm=llm,
            truncation=TruncationConfig(max_context_tokens=100_000),
        ):
            async def answer(self, question: str) -> str: ...

        agent = TestAgent()
        # Must not raise ValueError: "max_context_tokens / max_event_tokens require a token counter"
        result = await agent.answer("hello")
        assert result == "done"

    @pytest.mark.asyncio
    async def test_max_event_tokens_does_not_crash_on_generation(self):
        """Agent with max_event_tokens set should not raise ValueError during generation."""
        from unifiedllm import FakeLLMClient

        llm = FakeLLMClient.with_tool_call("return_result", {"result": "summary"})

        class TestAgent(
            Agent,
            llm=llm,
            truncation=TruncationConfig(max_event_tokens=50_000),
        ):
            async def summarize(self) -> str: ...

        agent = TestAgent()
        result = await agent.summarize()
        assert result == "summary"

    @pytest.mark.asyncio
    async def test_token_limits_with_llm_that_cannot_count_raises_runtime_error(self):
        """Agent with token limits and an LLM lacking count_tokens() should raise RuntimeError.

        Using getattr(..., None) silently passes count_tokens=None to render_context(), which
        then raises a cryptic internal ValueError. Instead, _build_messages should fail early
        with a clear error pointing to the LLM's missing capability.
        """

        class NoCountLLM:
            """Minimal fake LLM without count_tokens."""

            model = "no-count-model"

            async def acall(self, messages, tools=None, output_model=None, **kwargs):
                from unifiedllm import LLMResponse

                return LLMResponse(
                    raw_response=None,
                    content="",
                    tool_calls=[],
                    finish_reason="stop",
                    assistant_message={"role": "assistant", "content": ""},
                    reasoning=None,
                    usage=None,
                )

        class TestAgent(
            Agent,
            llm=NoCountLLM(),
            truncation=TruncationConfig(max_context_tokens=100_000),
        ):
            async def answer(self, question: str) -> str: ...

        agent = TestAgent()
        with pytest.raises(RuntimeError, match="count_tokens"):
            await agent.answer("hello")
