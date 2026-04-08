"""Tests for code validation.

Focus on:
- AST validation (forbidden features)
- Code validation retry with error context
- Validation error messages
"""

import pytest

from agent006 import Agent
from agent006.errors import ValidationError
from agent006.runtime.actor import ActorRuntime
from unifiedllm import FakeLLMClient

# Module-level test LLM (can be overridden at instantiation)
_TEST_LLM = FakeLLMClient()


class SimpleAgent(Agent, llm=_TEST_LLM):
    """Test agent for validation tests."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.count = 0


@pytest.mark.asyncio
async def test_validation_rejects_forbidden_imports():
    """Test that validation rejects forbidden imports."""
    agent_instance = SimpleAgent()
    runtime = ActorRuntime(agent_instance)

    # Try to execute code with forbidden import
    code = "import os\nself.count = 1"

    result = await runtime.execute_code(code, validate=True)

    assert result.error is not None
    assert isinstance(result.error, ValidationError)
    assert "import" in str(result.error).lower() or "forbidden" in str(result.error).lower()


@pytest.mark.asyncio
async def test_validation_rejects_exec_eval():
    """Test that validation rejects exec() and eval()."""
    agent_instance = SimpleAgent()
    runtime = ActorRuntime(agent_instance)

    # Try to execute code with exec
    code = "exec('self.count = 1')"

    result = await runtime.execute_code(code, validate=True)

    assert result.error is not None
    assert isinstance(result.error, ValidationError)
    assert "exec" in str(result.error).lower() or "forbidden" in str(result.error).lower()
