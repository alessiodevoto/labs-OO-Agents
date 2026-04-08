"""
Test cases for reserved parameter name validation.

This test suite verifies that @strategy methods cannot use reserved names
like 'reasoning' and 'message' as parameter names, since these would
shadow the builtin functions available in generated code.
"""

import pytest

from nemo_oo_agents import Agent, PurePythonStrategy, strategy
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


def test_message_parameter_raises_value_error():
    """Test that using 'message' as a parameter name raises ValueError."""

    test_llm = FakeLLMClient()

    # Defining a class with 'message' parameter should raise ValueError
    with pytest.raises(ValueError) as exc_info:

        class TestAgent(Agent, llm=test_llm):
            """Test agent with message parameter."""

            async def process_with_message(self, message: str) -> str:
                """This should fail - message is reserved."""
                ...

    error_msg = str(exc_info.value)
    assert "reserved parameter name" in error_msg.lower()
    assert "message" in error_msg


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


def test_burger_order_scenario_fails_at_definition():
    """Test that the burger order scenario fails when class is defined."""

    test_llm = FakeLLMClient()

    # The problematic method definition should fail immediately
    with pytest.raises(ValueError) as exc_info:

        class OrderAgent(Agent, llm=test_llm):
            """Agent for processing food orders."""

            async def add_item(self, item: str) -> None:
                """Add an item to the order."""
                pass

            async def process_request(self, message: str) -> dict:
                """This should fail - message is reserved."""
                ...

    error_msg = str(exc_info.value)
    assert "message" in error_msg
    assert "reserved" in error_msg.lower()


@pytest.mark.asyncio
async def test_safe_parameter_names_work():
    """Test that using non-reserved parameter names works fine."""

    code = """
reasoning("Processing the customer request")

# Access parameter with safe name
result = f"Processed: {request}"

# Call message() function - works because no conflict
message("Task completed successfully")

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


def test_multiple_params_one_reserved():
    """Test that error is raised even when only one param is reserved."""

    test_llm = FakeLLMClient()

    with pytest.raises(ValueError) as exc_info:

        class TestAgent(Agent, llm=test_llm):
            async def process(self, text: str, message: str, count: int) -> str:
                """Should fail because of 'message' parameter."""
                ...

    error_msg = str(exc_info.value)
    assert "message" in error_msg
    assert "reserved" in error_msg.lower()


def test_both_reserved_names():
    """Test that using both reserved names raises error."""

    test_llm = FakeLLMClient()

    with pytest.raises(ValueError) as exc_info:

        class TestAgent(Agent, llm=test_llm):
            async def process(self, reasoning: str, message: str) -> str:
                """Should fail - both params are reserved."""
                ...

    # Error should mention reserved names (at least one of them)
    error_msg = str(exc_info.value).lower()
    assert "reserved" in error_msg
    assert "reasoning" in error_msg or "message" in error_msg


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
            async def process(self, message: str) -> str: ...

    error_msg = str(exc_info.value).lower()
    # Should provide helpful suggestions
    assert "suggestion" in error_msg or "instead" in error_msg or "use" in error_msg
