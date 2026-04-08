"""Tests for runtime define_method() tool."""

import pytest

from nemo_oo_agents import Agent
from unifiedllm import FakeLLMClient

# Module-level test LLM (can be overridden at instantiation)
_TEST_LLM = FakeLLMClient()


@pytest.mark.asyncio
async def test_define_method_basic():
    """Test basic method definition."""

    class TestAgent(Agent, llm=_TEST_LLM):
        async def main(self) -> str:
            # Define a helper method
            await self.runtime.define_method(
                name="get_greeting",
                params=["name: str"],
                return_type="str",
                docstring="Generate a greeting",
            )
            # Call it
            result = await self.get_greeting("World")
            return result

    agent_instance = TestAgent()

    # For MVP, the method is defined but calling it would need generation
    # Just verify the method exists
    await agent_instance.main()

    # Check method was added
    assert hasattr(agent_instance, "get_greeting")
    method = agent_instance.get_greeting

    # Check metadata
    from nemo_oo_agents.strategies import CodeActStrategy

    assert hasattr(method, "_agent_decorator")
    assert method._agent_decorator == "plan"
    assert isinstance(method._plan_strategy, CodeActStrategy)
    assert method._needs_generation is True


@pytest.mark.asyncio
async def test_define_method_no_params():
    """Test method definition with no parameters."""

    class TestAgent(Agent, llm=_TEST_LLM):
        async def setup(self) -> None:
            await self.runtime.define_method(
                name="get_status",
                params=[],
                return_type="str",
                docstring="Get current status",
            )

    agent_instance = TestAgent()

    await agent_instance.setup()

    # Method should exist
    assert hasattr(agent_instance, "get_status")
    method = agent_instance.get_status

    # Should be callable
    assert callable(method)


@pytest.mark.asyncio
async def test_define_method_multiple():
    """Test defining multiple methods."""

    class TestAgent(Agent, llm=_TEST_LLM):
        async def setup_all(self) -> None:
            await self.runtime.define_method(
                name="helper1",
                params=["x: int"],
                return_type="int",
                docstring="Helper 1",
            )
            await self.runtime.define_method(
                name="helper2",
                params=["y: str"],
                return_type="str",
                docstring="Helper 2",
            )

    agent_instance = TestAgent()

    await agent_instance.setup_all()

    # Both methods should exist
    assert hasattr(agent_instance, "helper1")
    assert hasattr(agent_instance, "helper2")

    # Both should be distinct
    assert agent_instance.helper1 != agent_instance.helper2


@pytest.mark.asyncio
async def test_define_method_with_body():
    """Test defining method with body in one call."""

    class TestAgent(Agent, llm=_TEST_LLM):
        async def setup(self) -> None:
            # Define with implementation
            await self.runtime.define_method(
                name="add_numbers",
                params=["a: int", "b: int"],
                return_type="int",
                docstring="Add two numbers",
                body="return a + b",
            )

    agent_instance = TestAgent()

    await agent_instance.setup()

    # Method should exist and not need generation
    assert hasattr(agent_instance, "add_numbers")
    method = agent_instance.add_numbers
    assert method._needs_generation is False

    # Should be callable
    result = await method(5, 3)
    assert result == 8


@pytest.mark.asyncio
async def test_define_method_with_body_multiple():
    """Test defining multiple methods with bodies (LLM batch pattern)."""

    class TestAgent(Agent, llm=_TEST_LLM):
        async def setup_all(self) -> None:
            # LLM sends multiple tool calls at once
            await self.runtime.define_method(
                name="double",
                params=["x: int"],
                return_type="int",
                docstring="Double a number",
                body="return x * 2",
            )
            await self.runtime.define_method(
                name="triple",
                params=["x: int"],
                return_type="int",
                docstring="Triple a number",
                body="return x * 3",
            )
            await self.runtime.define_method(
                name="square",
                params=["x: int"],
                return_type="int",
                docstring="Square a number",
                body="return x * x",
            )

    agent_instance = TestAgent()

    await agent_instance.setup_all()

    # All should be implemented
    assert await agent_instance.double(5) == 10
    assert await agent_instance.triple(5) == 15
    assert await agent_instance.square(5) == 25


@pytest.mark.asyncio
async def test_defined_method_with_body_persists():
    """Test that dynamically defined methods with bodies persist across calls.

    When a method is defined with a body, it should:
    1. Be callable immediately
    2. Not require LLM regeneration
    3. Persist and work the same on subsequent calls
    """

    class TestAgent(Agent, llm=_TEST_LLM):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.call_count = 0
            self.accumulator = 0

        async def setup(self) -> None:
            # Define a method that tracks call count using self
            await self.runtime.define_method(
                name="track_and_add",
                params=["x: int"],
                return_type="int",
                docstring="Track calls and add to accumulator",
                body="self.call_count += 1; self.accumulator += x; return self.accumulator",
            )

    agent_instance = TestAgent()

    # Setup defines the method
    await agent_instance.setup()

    # Method should be defined and not need generation
    assert hasattr(agent_instance, "track_and_add")
    method = agent_instance.track_and_add
    assert method._needs_generation is False

    # Call multiple times - should persist and work correctly
    result1 = await agent_instance.track_and_add(10)
    assert result1 == 10
    assert agent_instance.call_count == 1

    result2 = await agent_instance.track_and_add(5)
    assert result2 == 15  # 10 + 5
    assert agent_instance.call_count == 2

    result3 = await agent_instance.track_and_add(3)
    assert result3 == 18  # 10 + 5 + 3
    assert agent_instance.call_count == 3

    # No LLM calls should have been made (beyond any in setup)
    # The defined method executes directly without generation
