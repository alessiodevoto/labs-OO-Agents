"""Tests for default strategy override functionality.

The get_default_strategy() and set_default_strategy() functions allow
overriding the default strategy (CodeActStrategy) globally without
modifying agent classes.

Tests include:
- Unit tests for get/set functions (TestGetDefaultStrategy, TestSetDefaultStrategy)
- Integration tests verifying agents actually use the override (TestAgentUsesDefaultStrategy)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nemo_oo_agents import (
    Agent,
    CodeActStrategy,
    ReflexionStrategy,
    get_default_strategy,
    set_default_strategy,
)
from nemo_oo_agents.config import CodeActConfig
from nemo_oo_agents.strategies.pure_python import PurePythonStrategy


class TestGetDefaultStrategy:
    """Tests for get_default_strategy()."""

    def test_returns_codeact_by_default(self):
        """Default strategy should be CodeActStrategy when not overridden."""
        # Reset to ensure clean state
        set_default_strategy(None)

        strategy = get_default_strategy()

        assert isinstance(strategy, CodeActStrategy)

    def test_returns_fresh_instance_each_call(self):
        """Each call should return a fresh CodeActStrategy instance."""
        set_default_strategy(None)

        strategy1 = get_default_strategy()
        strategy2 = get_default_strategy()

        # Both should be CodeActStrategy
        assert isinstance(strategy1, CodeActStrategy)
        assert isinstance(strategy2, CodeActStrategy)
        # But different instances (fresh each time)
        assert strategy1 is not strategy2


class TestSetDefaultStrategy:
    """Tests for set_default_strategy()."""

    def test_override_with_codeact_strategy(self):
        """Should be able to override default to CodeActStrategy."""
        set_default_strategy(CodeActStrategy(config=CodeActConfig(max_iterations=10)))

        strategy = get_default_strategy()

        assert isinstance(strategy, CodeActStrategy)
        assert strategy.config.max_iterations == 10

    def test_override_with_reflexion_strategy(self):
        """Should be able to override default to ReflexionStrategy."""
        set_default_strategy(ReflexionStrategy())

        strategy = get_default_strategy()

        assert isinstance(strategy, ReflexionStrategy)

    def test_reset_to_default_with_none(self):
        """Setting None should reset to CodeActStrategy default."""
        # First override to PurePython
        set_default_strategy(PurePythonStrategy())
        assert isinstance(get_default_strategy(), PurePythonStrategy)

        # Then reset
        set_default_strategy(None)

        strategy = get_default_strategy()
        assert isinstance(strategy, CodeActStrategy)

    def test_override_returns_same_instance(self):
        """When overridden, should return the same instance (not fresh)."""
        original = CodeActStrategy(config=CodeActConfig(max_iterations=15))
        set_default_strategy(original)

        strategy1 = get_default_strategy()
        strategy2 = get_default_strategy()

        # Should return the same instance
        assert strategy1 is original
        assert strategy2 is original

    def test_multiple_overrides(self):
        """Should be able to override multiple times."""
        set_default_strategy(CodeActStrategy(config=CodeActConfig()))
        assert isinstance(get_default_strategy(), CodeActStrategy)

        set_default_strategy(ReflexionStrategy())
        assert isinstance(get_default_strategy(), ReflexionStrategy)

        set_default_strategy(PurePythonStrategy())
        assert isinstance(get_default_strategy(), PurePythonStrategy)


class TestAgentUsesDefaultStrategy:
    """Integration tests: verify agents actually use set_default_strategy() override.

    These tests caught the bug where metaclass.py had its own strategy resolution
    that didn't use get_default_strategy().
    """

    @pytest.mark.asyncio
    async def test_agent_method_uses_overridden_strategy(self):
        """Agent methods without @strategy should use set_default_strategy() override.

        This is the key integration test that verifies the strategy override
        actually affects agent execution, not just the getter function.
        """
        # Create a mock LLM
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "return 'test'"
        mock_llm.complete = AsyncMock(return_value=mock_response)

        # Define agent class (no explicit @strategy decorator)
        class TestAgent(Agent, llm=mock_llm):
            async def do_something(self) -> str:
                """A test method."""
                ...

        # Set override to CodeActStrategy
        set_default_strategy(CodeActStrategy(config=CodeActConfig(max_iterations=5)))

        # Create agent instance
        agent = TestAgent()

        # Patch the strategy's execute method to verify it's called
        with patch.object(CodeActStrategy, "execute", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = "result"

            # Call the agent method
            await agent.do_something()

            # Verify CodeActStrategy.execute was called (not PurePythonStrategy)
            assert mock_execute.called, (
                "CodeActStrategy.execute should be called when set_default_strategy(CodeActStrategy()) is used"
            )

    @pytest.mark.asyncio
    async def test_agent_uses_codeact_when_no_override(self):
        """Without override, agent should use CodeActStrategy."""
        # Create a mock LLM with proper async support
        mock_llm = MagicMock()
        mock_llm.acall = AsyncMock()

        # Define agent class
        class TestAgent(Agent, llm=mock_llm):
            async def do_something(self) -> str:
                """A test method."""
                ...

        # Ensure no override (use default)
        set_default_strategy(None)

        # Create agent instance
        agent = TestAgent()

        # Patch CodeActStrategy's execute method
        with patch.object(CodeActStrategy, "execute", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = "result"

            # Call the agent method
            await agent.do_something()

            # Verify CodeActStrategy.execute was called
            assert mock_execute.called, "CodeActStrategy.execute should be called by default"


@pytest.fixture(autouse=True)
def reset_default_strategy():
    """Reset default strategy before and after each test."""
    set_default_strategy(None)
    yield
    set_default_strategy(None)
