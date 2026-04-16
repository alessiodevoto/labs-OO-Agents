"""Test that helper methods have consistent execution namespace (includes asyncio).

This test addresses the issue documented in .cursor/plans/fix_empty_trace_issue_8f8a334c.plan.md
where helper methods were compiled without asyncio in their globals, causing NameError.
"""

import pytest

from nemo_oo_agents import Agent, strategy
from nemo_oo_agents.strategies.pure_python import PurePythonStrategy
from unifiedllm import FakeLLMClient


@pytest.mark.asyncio
async def test_helper_method_can_use_asyncio_gather():
    """Test that a prebound helper method can call asyncio.gather().

    This verifies that the helper method's execution namespace includes asyncio
    and matches ActorRuntime.execute_code() globals.
    """
    # LLM generates a helper that uses asyncio.gather()
    code = """
# Define a helper that processes items in parallel
async def process_parallel(self, items: list[str]) -> list[str]:
    tasks = [self.process_single(item) for item in items]
    results = await asyncio.gather(*tasks)
    return results

# Use the helper
result = await self.process_parallel(texts)
return result
"""

    llm = FakeLLMClient.with_code_responses([code])

    class ParallelAgent(Agent, llm=llm):
        """Agent that processes items in parallel."""

        async def process_single(self, item: str) -> str:
            """Process a single item."""
            return f"processed_{item}"

        @strategy(PurePythonStrategy())
        async def process_batch(self, texts: list[str]) -> list[str]:
            """Process multiple items using parallel helper."""
            ...

    agent_instance = ParallelAgent()
    result = await agent_instance.process_batch(["a", "b", "c"])

    # Verify the helper worked correctly
    assert result == ["processed_a", "processed_b", "processed_c"]

    # Verify the helper was installed
    assert hasattr(agent_instance, "process_parallel")


@pytest.mark.asyncio
async def test_helper_method_can_use_doc_introspection():
    """Test that helper methods can access agentdoc helpers (doc, brief, etc).

    This verifies that helper methods have access to agentdoc introspection functions
    that are part of ExecutionNamespaceBuilder.
    """
    # LLM generates a helper that uses doc() from agentdoc
    code = """


async def get_agent_info(self) -> str:
    # Use doc() to introspect the agent
    return doc(self)

result = await self.get_agent_info()
return result[:50]  # Return first 50 chars
"""

    llm = FakeLLMClient.with_code_responses([code])

    class IntrospectionAgent(Agent, llm=llm):
        """Agent that uses introspection."""

        @strategy(PurePythonStrategy())
        async def get_info(self) -> str:
            """Get agent info using doc()."""
            ...

    agent_instance = IntrospectionAgent()
    result = await agent_instance.get_info()

    # Verify the helper worked correctly (doc() returns agent description)
    assert isinstance(result, str)
    assert len(result) > 0
