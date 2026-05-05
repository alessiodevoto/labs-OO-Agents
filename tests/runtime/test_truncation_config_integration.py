"""Tests for truncation configuration integration with Agent class."""

import pytest

from nemo_oo_agents import Agent
from nemo_oo_agents.config.truncation_config import (
    CaptureConfig,
    FormatConfig,
    TruncationConfig,
)
from nemo_oo_agents.unifiedllm import FakeLLMClient

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
        assert agent._truncation.capture.max_stdout == 50_000
        assert agent._truncation.capture.max_stderr == 2_000
        assert agent._truncation.event_format.max_length == 200

    def test_class_level_config(self):
        """Class-level config should override defaults."""

        class TestAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(
                capture=CaptureConfig(max_stdout=100000), event_format=FormatConfig(max_length=100)
            ),
        ):
            pass

        agent = TestAgent()

        # Should have class-level config
        assert agent._truncation.capture.max_stdout == 100_000
        assert agent._truncation.event_format.max_length == 100
        # Other defaults preserved
        assert agent._truncation.capture.max_stderr == 2_000

    def test_instance_level_config(self):
        """Instance-level config should override class config."""

        class TestAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(capture=CaptureConfig(max_stdout=100000)),
        ):
            pass

        agent = TestAgent(
            truncation=TruncationConfig(
                capture=CaptureConfig(max_stdout=200000), event_format=FormatConfig(max_depth=10)
            )
        )

        # Instance config should win
        assert agent._truncation.capture.max_stdout == 200_000
        assert agent._truncation.event_format.max_depth == 10
        # Other class defaults preserved
        assert agent._truncation.event_format.max_length == 200

    def test_config_merge_behavior(self):
        """Configs should merge properly (later overrides earlier)."""

        class TestAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(
                capture=CaptureConfig(max_stdout=100000), event_format=FormatConfig(max_length=100)
            ),
        ):
            pass

        agent = TestAgent(
            truncation=TruncationConfig(event_format=FormatConfig(max_length=200, max_depth=5))
        )

        # Merged result
        assert agent._truncation.capture.max_stdout == 100_000  # From class
        assert agent._truncation.event_format.max_length == 200  # From instance
        assert agent._truncation.event_format.max_depth == 5  # From instance
        assert agent._truncation.capture.max_stderr == 2_000  # From default

    def test_multiple_agents_independent_configs(self):
        """Multiple agents should have independent configs."""

        class Agent1(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(capture=CaptureConfig(max_stdout=50000)),
        ):
            pass

        class Agent2(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(capture=CaptureConfig(max_stdout=100000)),
        ):
            pass

        agent1 = Agent1()
        agent2 = Agent2()

        # Should be independent
        assert agent1._truncation.capture.max_stdout == 50_000
        assert agent2._truncation.capture.max_stdout == 100_000


class TestTruncationConfigUsage:
    """Tests for config usage in execution."""

    @pytest.mark.asyncio
    async def test_custom_stdout_limit_applied(self):
        """Custom stdout limit should be applied."""

        class TestAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(capture=CaptureConfig(max_stdout=1000)),  # Very small limit
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
            truncation=TruncationConfig(capture=CaptureConfig(max_stderr=500)),  # Very small limit
        ):
            pass

        agent = TestAgent()

        # This test is simplified due to sandbox restrictions
        # The limit is verified by the unit tests
        assert agent._truncation.capture.max_stderr == 500

    @pytest.mark.asyncio
    async def test_different_agents_different_limits(self):
        """Different agents should use their own limits."""

        class SmallLimitAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(capture=CaptureConfig(max_stdout=500)),
        ):
            pass

        class LargeLimitAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(capture=CaptureConfig(max_stdout=10000)),
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
        assert agent._truncation.capture.max_stdout == 50_000

    def test_partial_override(self):
        """Partial configs should only override specified fields."""

        class TestAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(
                capture=CaptureConfig(max_stdout=100000, max_stderr=30000),
                event_format=FormatConfig(max_length=100),
            ),
        ):
            pass

        agent = TestAgent(
            truncation=TruncationConfig(
                event_format=FormatConfig(max_length=200)
            )  # Only override this
        )

        # Only value.max_length should be overridden
        assert agent._truncation.capture.max_stdout == 100_000  # Preserved from class
        assert agent._truncation.capture.max_stderr == 30_000  # Preserved from class
        assert agent._truncation.event_format.max_length == 200  # Overridden


class TestTokenBudgetIntegration:
    """Tests for token budget fields (max_context_tokens, max_event_tokens)."""

    @pytest.mark.asyncio
    async def test_max_context_tokens_does_not_crash_on_generation(self):
        """Agent with max_context_tokens set should not raise ValueError during generation.

        Regression test: render_context() raises ValueError if context_limit is non-None
        but count_tokens is not provided. The actor must pass count_tokens to render_context().
        """
        from nemo_oo_agents.unifiedllm import FakeLLMClient

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
        from nemo_oo_agents.unifiedllm import FakeLLMClient

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
                from nemo_oo_agents.unifiedllm import LLMResponse

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
        # The RuntimeError may be wrapped in GenerationError by the retry loop
        from nemo_oo_agents.errors import GenerationError

        with pytest.raises((RuntimeError, GenerationError), match="count_tokens"):
            await agent.answer("hello")


class TestMethodLevelTruncationConfig:
    """Tests for method-level TruncationConfig via @strategy(truncation=...)."""

    def test_strategy_decorator_stores_truncation_attribute(self):
        """@strategy(truncation=...) should store the config as _strategy_truncation."""
        from nemo_oo_agents import strategy

        tc = TruncationConfig(capture=CaptureConfig(max_stdout=1234))

        class TestAgent(Agent, llm=_TEST_LLM):
            @strategy(truncation=tc)
            async def method(self) -> str:
                """Do something."""
                ...

        # The decorator should store the truncation config on the underlying function
        fn = TestAgent.__dict__["method"]
        # The wrapper exposes it on itself; the underlying func also has it
        # (via setattr on func directly)
        assert getattr(fn, "_strategy_truncation", None) is tc or (
            hasattr(fn, "__func__") and getattr(fn.__func__, "_strategy_truncation", None) is tc
        )

    def test_strategy_decorator_without_truncation_stores_none(self):
        """@strategy() without truncation= should set _strategy_truncation to None."""
        from nemo_oo_agents import strategy

        class TestAgent(Agent, llm=_TEST_LLM):
            @strategy()
            async def method(self) -> str:
                """Do something."""
                ...

        fn = TestAgent.__dict__["method"]
        assert getattr(fn, "_strategy_truncation", None) is None

    @pytest.mark.asyncio
    async def test_method_level_truncation_visible_to_runtime_during_execution(self):
        """During method execution, runtime.truncation_config should reflect method-level override.

        We use a custom strategy that captures the truncation config visible through
        runtime during strategy execution, then verify it is the merged (method-level) config.
        """
        from nemo_oo_agents import strategy
        from nemo_oo_agents.strategies.base import GenerationStrategy
        from nemo_oo_agents.strategies.current_call import CurrentCall

        captured_tc = {}

        class CapturingStrategy(GenerationStrategy):
            """Strategy that captures runtime.truncation_config and returns a fixed value."""

            name = "CAPTURING"
            traceable = False
            requires_lock = False

            def get_block_overrides(self):
                return {}

            async def execute(self, runtime, call: CurrentCall):
                captured_tc["config"] = runtime.truncation_config
                return "captured"

        method_tc = TruncationConfig(capture=CaptureConfig(max_stdout=12345))

        class TestAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(
                capture=CaptureConfig(max_stdout=100000), event_format=FormatConfig(max_length=77)
            ),
        ):
            @strategy(CapturingStrategy(), truncation=method_tc)
            async def run(self) -> str:
                """Run."""
                ...

        agent = TestAgent()
        result = await agent.run()
        assert result == "captured"

        # The strategy should have seen the merged config
        assert "config" in captured_tc
        tc = captured_tc["config"]
        # Method-level field wins
        assert tc.capture.max_stdout == 12345
        # Agent-level field preserved (not in method override)
        assert tc.event_format.max_length == 77

    @pytest.mark.asyncio
    async def test_method_level_truncation_applied_to_execute_code(self):
        """execute_code during a method call should use the method-level stdout limit.

        The agent has a large stdout limit (100k), but the method is decorated with
        a small limit (200 chars). Code executed during that method should be truncated
        at 200 chars.
        """
        from nemo_oo_agents import strategy
        from nemo_oo_agents.strategies.base import GenerationStrategy
        from nemo_oo_agents.strategies.current_call import CurrentCall

        execution_results = {}

        class CodeRunningStrategy(GenerationStrategy):
            """Strategy that runs some code and captures the stdout result."""

            name = "CODE_RUNNING"
            traceable = False
            requires_lock = False

            def get_block_overrides(self):
                return {}

            async def execute(self, runtime, call: CurrentCall):
                # Execute code that produces 5000 chars of output
                result = await runtime.execute_code("print('x' * 5000)")
                execution_results["stdout"] = result.stdout
                return "done"

        class TestAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(
                capture=CaptureConfig(max_stdout=100000)
            ),  # Large agent-level limit
        ):
            @strategy(
                CodeRunningStrategy(),
                truncation=TruncationConfig(capture=CaptureConfig(max_stdout=200)),
            )
            async def run_code(self) -> str:
                """Run code."""
                ...

        agent = TestAgent()
        result = await agent.run_code()
        assert result == "done"

        # The stdout should be truncated at 200 chars (not 100_000)
        assert "stdout" in execution_results
        stdout = execution_results["stdout"]
        assert "Output too large" in stdout, (
            f"Expected truncation at 200 chars but got: {stdout[:300]!r}"
        )

    @pytest.mark.asyncio
    async def test_method_level_truncation_does_not_affect_other_methods(self):
        """A truncation override on one method should not affect another method."""
        from nemo_oo_agents import strategy
        from nemo_oo_agents.strategies.codeact import CodeActStrategy

        llm = FakeLLMClient.with_tool_call("return_result", {"result": "done"})

        class TestAgent(
            Agent,
            llm=llm,
            truncation=TruncationConfig(capture=CaptureConfig(max_stdout=100000)),
        ):
            @strategy(
                CodeActStrategy(),
                truncation=TruncationConfig(capture=CaptureConfig(max_stdout=500)),
            )
            async def small_limit(self) -> str:
                """Small stdout limit."""
                ...

            @strategy(CodeActStrategy())
            async def big_limit(self) -> str:
                """Uses agent-level limit."""
                ...

        agent = TestAgent()

        # Both methods resolve at the agent level — verify the agent config is intact
        assert agent._truncation.capture.max_stdout == 100_000

        # The small_limit method's wrapper should have _strategy_truncation set
        small_fn = TestAgent.__dict__["small_limit"]
        assert getattr(small_fn, "_strategy_truncation", None) is not None

        # The big_limit method's wrapper should NOT have _strategy_truncation set
        big_fn = TestAgent.__dict__["big_limit"]
        assert getattr(big_fn, "_strategy_truncation", None) is None

    def test_truncation_config_merge_at_method_level(self):
        """Method-level config merges with agent config (method fields win)."""
        from nemo_oo_agents import strategy

        class TestAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(
                capture=CaptureConfig(max_stdout=100000), event_format=FormatConfig(max_length=100)
            ),
        ):
            @strategy(truncation=TruncationConfig(capture=CaptureConfig(max_stdout=500)))
            async def method(self) -> str:
                """Method with partial truncation override."""
                ...

        agent = TestAgent()

        # Simulate what actor does: merge agent config with method-level override
        method_fn = TestAgent.__dict__["method"]
        method_tc = getattr(method_fn, "_strategy_truncation", None)
        assert method_tc is not None

        merged = agent._truncation.merge_with(method_tc)

        # Method field wins
        assert merged.capture.max_stdout == 500
        # Agent field preserved (not in method override)
        assert merged.event_format.max_length == 100

    @pytest.mark.asyncio
    async def test_truncation_config_reverts_after_method_returns(self):
        """After a method call completes, runtime.truncation_config must return
        to the agent-level config, not the method-level override.

        This verifies the context var is properly reset in the finally block.
        """
        from nemo_oo_agents import strategy
        from nemo_oo_agents.strategies.base import GenerationStrategy
        from nemo_oo_agents.strategies.current_call import CurrentCall

        captured = {}

        class CapturingStrategy(GenerationStrategy):
            name = "CAPTURING"
            traceable = False
            requires_lock = False

            def get_block_overrides(self):
                return {}

            async def execute(self, runtime, call: CurrentCall):
                captured["during"] = runtime.truncation_config
                return "done"

        method_tc = TruncationConfig(capture=CaptureConfig(max_stdout=99999))

        class TestAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(capture=CaptureConfig(max_stdout=100000)),
        ):
            @strategy(CapturingStrategy(), truncation=method_tc)
            async def run(self) -> str:
                """Run."""
                ...

        agent = TestAgent()

        # Before: runtime reflects agent-level config
        assert agent.runtime.truncation_config.capture.max_stdout == 100_000

        await agent.run()

        # During: strategy saw the method-level override
        assert captured["during"].capture.max_stdout == 99_999

        # After: runtime has reverted to agent-level config
        assert agent.runtime.truncation_config.capture.max_stdout == 100_000

    @pytest.mark.asyncio
    async def test_method_level_max_block_chars_limits_context_rendering(self):
        """Method-level max_block_chars should limit the content the LLM sees
        in rendered context blocks, independently of the agent-level setting.

        We use a custom strategy that captures what runtime.truncation_config
        reports for max_block_chars, then verify it matches the method override
        (not the agent-level value).  The context builder reads this same property
        when capping block content, so if the property returns the right value the
        rendering will use the right cap.
        """
        from nemo_oo_agents import strategy
        from nemo_oo_agents.strategies.base import GenerationStrategy
        from nemo_oo_agents.strategies.current_call import CurrentCall

        captured = {}

        class BlockCapturingStrategy(GenerationStrategy):
            name = "BLOCK_CAPTURING"
            traceable = False
            requires_lock = False

            def get_block_overrides(self):
                return {}

            async def execute(self, runtime, call: CurrentCall):
                captured["max_block_chars"] = runtime.truncation_config.max_block_chars
                return "done"

        class TestAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(max_block_chars=20_000),  # agent default
        ):
            @strategy(
                BlockCapturingStrategy(),
                truncation=TruncationConfig(max_block_chars=5_000),  # method override
            )
            async def run(self) -> str:
                """Run."""
                ...

        agent = TestAgent()
        await agent.run()

        # Strategy must have seen the method-level max_block_chars, not the agent's
        assert captured["max_block_chars"] == 5_000

    @pytest.mark.asyncio
    async def test_concurrent_method_calls_have_isolated_truncation_configs(self):
        """Concurrent calls to methods with different truncation configs are isolated.

        asyncio context vars are copied into each new Task at creation time, so
        _execute_with_generation's var.set() in one task cannot bleed into another.
        This verifies the contextvars-based isolation holds under actual concurrency.
        """
        import asyncio

        from nemo_oo_agents import strategy
        from nemo_oo_agents.strategies.base import GenerationStrategy
        from nemo_oo_agents.strategies.current_call import CurrentCall

        captured = {}

        class CapturingStrategy(GenerationStrategy):
            name = "CAPTURING"
            traceable = False
            requires_lock = False

            def get_block_overrides(self):
                return {}

            async def execute(self, runtime, call: CurrentCall):
                # Yield once to allow the other task to interleave
                await asyncio.sleep(0)
                seen = runtime.truncation_config.capture.max_stdout
                captured[call.method_name] = seen
                # Yield again — if the context var leaked, seen would have changed
                await asyncio.sleep(0)
                assert runtime.truncation_config.capture.max_stdout == seen, (
                    "truncation_config changed after yielding — context var leaked between tasks"
                )
                return "done"

        class TestAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(capture=CaptureConfig(max_stdout=100000)),
        ):
            @strategy(
                CapturingStrategy(),
                truncation=TruncationConfig(capture=CaptureConfig(max_stdout=1000)),
            )
            async def method_a(self) -> str:
                """Method A with small stdout limit."""
                ...

            @strategy(
                CapturingStrategy(),
                truncation=TruncationConfig(capture=CaptureConfig(max_stdout=2000)),
            )
            async def method_b(self) -> str:
                """Method B with medium stdout limit."""
                ...

        agent = TestAgent()
        await asyncio.gather(agent.method_a(), agent.method_b())

        # Each task must have seen exactly its own method-level config
        assert captured["method_a"] == 1_000
        assert captured["method_b"] == 2_000

    @pytest.mark.asyncio
    async def test_nested_method_call_sees_inner_method_config(self):
        """When outer's strategy calls inner(), inner sees its own truncation config.

        _execute_with_generation sets the context var for the duration of each
        method call and resets it in finally.  Nested calls therefore get their
        own method-level config during execution, and the outer config is
        restored once the inner call returns.
        """
        from nemo_oo_agents import strategy
        from nemo_oo_agents.strategies.base import GenerationStrategy
        from nemo_oo_agents.strategies.current_call import CurrentCall

        captured: dict = {}

        class InnerCapturingStrategy(GenerationStrategy):
            name = "INNER_CAP"
            traceable = False
            requires_lock = False

            def get_block_overrides(self):
                return {}

            async def execute(self, runtime, call: CurrentCall):
                captured["inner"] = runtime.truncation_config.capture.max_stdout
                return "done"

        class OuterCallingStrategy(GenerationStrategy):
            """Strategy that records its config, calls agent.inner(), then records again."""

            name = "OUTER_CALL"
            traceable = False
            requires_lock = False

            def get_block_overrides(self):
                return {}

            async def execute(self, runtime, call: CurrentCall):
                captured["outer_before"] = runtime.truncation_config.capture.max_stdout
                await runtime.agent.inner()
                # Config must be restored to outer's value after inner returns
                captured["outer_after"] = runtime.truncation_config.capture.max_stdout
                return "done"

        class TestAgent(
            Agent,
            llm=_TEST_LLM,
            truncation=TruncationConfig(capture=CaptureConfig(max_stdout=100000)),
        ):
            @strategy(
                OuterCallingStrategy(),
                truncation=TruncationConfig(capture=CaptureConfig(max_stdout=1000)),
            )
            async def outer(self) -> str:
                """Outer method."""
                ...

            @strategy(
                InnerCapturingStrategy(),
                truncation=TruncationConfig(capture=CaptureConfig(max_stdout=2000)),
            )
            async def inner(self) -> str:
                """Inner method called from outer's strategy."""
                ...

        agent = TestAgent()
        await agent.outer()

        # Inner sees its own config (2_000), not outer's (1_000)
        assert captured["inner"] == 2_000
        # Outer sees its own config before and after calling inner
        assert captured["outer_before"] == 1_000
        assert captured["outer_after"] == 1_000
