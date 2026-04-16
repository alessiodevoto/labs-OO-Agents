"""
Test cases for reserved parameter name validation.

This test suite verifies that @strategy methods cannot use reserved names
like 'reasoning' as parameter names, since these would shadow the builtin
functions available in generated code.

Note: 'message' was previously reserved but was removed when the message()
builtin was removed from CodeAct. Only 'reasoning' is now reserved.
"""

import pytest

from nemo_oo_agents import Agent, strategy
from nemo_oo_agents.strategies.pure_python import PurePythonStrategy
from unifiedllm import FakeLLMClient, LLMResponse


def _resp(content: str) -> LLMResponse:
    """Create a test LLM response with the given content."""
    return LLMResponse(
        raw_response=None,
        content=content,
        tool_calls=[],
        finish_reason="stop",
        assistant_message={"role": "assistant", "content": content},
    )


def test_reasoning_parameter_raises_value_error():
    """Test that using 'reasoning' as a parameter name raises ValueError."""

    test_llm = FakeLLMClient()

    # Defining a class with 'reasoning' parameter should raise ValueError
    with pytest.raises(ValueError) as exc_info:

        class TestAgent(Agent, llm=test_llm):
            """Test agent with reasoning parameter."""

            async def analyze_with_reasoning(self, reasoning: str) -> str:
                """This should fail - reasoning is reserved."""
                ...

    error_msg = str(exc_info.value)
    assert "reserved parameter name" in error_msg.lower()
    assert "reasoning" in error_msg


def test_message_parameter_is_no_longer_reserved():
    """Test that 'message' is no longer a reserved parameter name."""

    test_llm = FakeLLMClient()

    # 'message' was removed from reserved names when the message() builtin was removed
    # Defining a class with 'message' as an implemented (non-ellipsis) method should work fine
    class TestAgent(Agent, llm=test_llm):
        """Test agent - message param is now allowed."""

        async def process_with_message(self, message: str) -> str:
            """Implemented method - message is no longer reserved."""
            return f"Got: {message}"

    # Should work fine since message is no longer reserved
    agent = TestAgent()
    assert agent is not None


def test_burger_order_scenario_with_message_param():
    """Test that 'message' parameter in an implemented method works now."""

    test_llm = FakeLLMClient()

    # Previously this would fail; now it should work fine
    class OrderAgent(Agent, llm=test_llm):
        """Agent for processing food orders."""

        async def add_item(self, item: str) -> None:
            """Add an item to the order."""
            pass

        async def process_request(self, message: str) -> str:
            """Implemented - message param is fine now."""
            return f"Handled: {message}"

    agent = OrderAgent()
    assert agent is not None


@pytest.mark.asyncio
async def test_safe_parameter_names_work():
    """Test that using non-reserved parameter names works fine."""

    code = """
reasoning("Processing the customer request")

# Access parameter with safe name
result = f"Processed: {request}"

return result
"""

    test_llm = FakeLLMClient(scripted_responses=[_resp(code)])

    class TestAgent(Agent, llm=test_llm):
        """Test agent with safe parameter names."""

        @strategy(PurePythonStrategy())
        async def process_request(self, request: str) -> str:
            """Process a request (safe parameter name)."""
            ...

    agent_instance = TestAgent(llm=test_llm)

    # This should work fine - no reserved names
    result = await agent_instance.process_request("hello world")
    assert result == "Processed: hello world"


def test_multiple_params_only_reasoning_reserved():
    """Test that only 'reasoning' triggers reserved-name error, not 'message'."""

    test_llm = FakeLLMClient()

    # 'message' no longer reserved — should not raise
    class TestAgentOk(Agent, llm=test_llm):
        async def process(self, text: str, message: str, count: int) -> str:
            """Implemented method - message param allowed."""
            return text

    assert TestAgentOk is not None

    # 'reasoning' still reserved — should raise
    with pytest.raises(ValueError) as exc_info:

        class TestAgentBad(Agent, llm=test_llm):
            async def process(self, text: str, reasoning: str, count: int) -> str:
                """Should fail because of 'reasoning' parameter."""
                ...

    error_msg = str(exc_info.value)
    assert "reasoning" in error_msg
    assert "reserved" in error_msg.lower()


def test_reasoning_reserved_name():
    """Test that only reasoning triggers reserved-name error."""

    test_llm = FakeLLMClient()

    with pytest.raises(ValueError) as exc_info:

        class TestAgent(Agent, llm=test_llm):
            async def process(self, reasoning: str) -> str:
                """Should fail - reasoning is reserved."""
                ...

    error_msg = str(exc_info.value).lower()
    assert "reserved" in error_msg
    assert "reasoning" in error_msg


@pytest.mark.asyncio
async def test_implemented_methods_not_validated_for_reserved_names():
    """Test that implemented methods are not validated (only ellipsis methods are)."""

    test_llm = FakeLLMClient()

    # Implemented methods are NOT wrapped, so reserved names are allowed
    # Only ellipsis methods (which need generation) are validated
    class TestAgent(Agent, llm=test_llm):
        async def process(self, message: str) -> str:
            """Implemented method - not validated for reserved params."""
            return f"Got: {message}"

    # Should work fine since it's implemented
    agent = TestAgent()
    result = await agent.process("hello")
    assert result == "Got: hello"


def test_error_message_provides_suggestions():
    """Test that error message suggests alternative parameter names."""

    test_llm = FakeLLMClient()

    with pytest.raises(ValueError) as exc_info:

        class TestAgent(Agent, llm=test_llm):
            async def process(self, reasoning: str) -> str: ...

    error_msg = str(exc_info.value).lower()
    # Should provide helpful suggestions
    assert "suggestion" in error_msg or "instead" in error_msg or "use" in error_msg
